"""Public identity bridge APIs; platform credentials never become ordinary KG actors."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, Security
from fastapi.security import HTTPBearer
from pydantic import TypeAdapter, ValidationError

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.bridge.models import (
    AssertionRequest,
    BootstrapResult,
    ConnectionPage,
    ConnectionView,
    EventResult,
    IntegrationChange,
    IntegrationCreate,
    IntegrationPage,
    IntegrationView,
    LinkApproval,
    LinkPage,
    LinkView,
    RevisionCommand,
    SyncEvent,
    TokenRequest,
    TokenResult,
)
from domain_kg.identity.bridge.sync import synchronize
from domain_kg.identity.models import AccessContext, Action
from domain_kg.identity.routes import HTTPPageRequest, IdentityRuntime
from domain_kg.models import FrozenModel, IdempotencyKey


class IdentityError(FrozenModel):
    code: ErrorCode
    message: str
    retryable: bool
    request_id: UUID


def bearer(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise DomainError(ErrorCode.UNAUTHENTICATED, "A platform Bearer credential is required.")
    return token


def request_key(request: Request) -> str:
    try:
        return TypeAdapter(IdempotencyKey).validate_python(request.headers.get("idempotency-key"))
    except ValidationError:
        raise DomainError(
            ErrorCode.VALIDATION_ERROR, "A valid Idempotency-Key header is required."
        ) from None


def bridge_router(runtime: IdentityRuntime) -> APIRouter:
    router = APIRouter(
        tags=["Identity bridge"],
        dependencies=[Security(HTTPBearer(auto_error=False, scheme_name="BearerCredential"))],
        responses={
            status: {"model": IdentityError, "description": description}
            for status, description in {
                401: "Credential, assertion or connection is invalid or unavailable.",
                403: "Membership, action scope or delegation does not permit the operation.",
                404: "The selected resource is unavailable in the authenticated organization.",
                409: "Version, idempotency, cursor or policy conflict; inspect code and retryable.",
                422: "Request fields are invalid.",
                503: "Identity or authority backend unavailable.",
            }.items()
        },
    )
    bridge = runtime.bridge

    async def admin(request: Request, *, mutating: bool = False) -> AccessContext:
        principal, member = await runtime.actor(request, mutating=mutating)
        return await bridge.policy.resolve_access(
            principal, [], Action.MANAGE_ORGANIZATION, org_id=member.org_id
        )

    @router.get("/api/v1/identity-integration-profiles")
    async def profiles(request: Request, response: Response) -> dict[str, Any]:
        access = await admin(request)
        response.headers["Cache-Control"] = "no-store"
        return {
            "enabled": bridge.config is not None,
            "items": [
                {"id": str(p.id), "name": p.name}
                for p in bridge.config.profiles
                if access.org_id in p.allowed_org_ids
            ]
            if bridge.config
            else [],
        }

    @router.post("/api/v1/identity-integrations", status_code=201)
    async def create(
        request: Request, response: Response, command: IntegrationCreate
    ) -> IntegrationView:
        response.headers["Cache-Control"] = "no-store"
        return await bridge.control.create(await admin(request, mutating=True), command)

    @router.get("/api/v1/identity-integrations")
    async def integrations(
        request: Request, response: Response, page: Annotated[HTTPPageRequest, Query()]
    ) -> IntegrationPage:
        access = await admin(request)
        response.headers["Cache-Control"] = "no-store"
        result = await bridge.management.list_page(
            access.principal, access.org_id, "integrations", page
        )
        assert isinstance(result, IntegrationPage)
        return result

    @router.patch("/api/v1/identity-integrations/{integration_id}")
    async def change(
        request: Request, response: Response, integration_id: UUID, command: IntegrationChange
    ) -> IntegrationView:
        response.headers["Cache-Control"] = "no-store"
        return await bridge.control.change(
            await admin(request, mutating=True), integration_id, command
        )

    @router.get("/api/v1/identity-integrations/{integration_id}/links")
    async def links(
        request: Request,
        response: Response,
        integration_id: UUID,
        page: Annotated[HTTPPageRequest, Query()],
    ) -> LinkPage:
        access = await admin(request)
        response.headers["Cache-Control"] = "no-store"
        result = await bridge.management.list_page(
            access.principal, access.org_id, "links", page, integration_id=integration_id
        )
        assert isinstance(result, LinkPage)
        return result

    @router.post("/api/v1/identity-links/{link_id}/approval")
    async def approve(
        request: Request, response: Response, link_id: UUID, command: LinkApproval
    ) -> LinkView:
        response.headers["Cache-Control"] = "no-store"
        return await bridge.management.approve(
            await admin(request, mutating=True), link_id, command
        )

    @router.post(
        "/api/v1/identity-bridge/bootstrap",
        openapi_extra={
            "parameters": [
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": True,
                    "schema": {"type": "string", "minLength": 1, "maxLength": 128},
                    "description": "Reuse exactly this key and body after a lost response.",
                }
            ]
        },
    )
    async def bootstrap(
        request: Request, response: Response, command: AssertionRequest
    ) -> BootstrapResult:
        token = bearer(request)
        response.headers["Cache-Control"] = "no-store"
        result = await bridge.process(token, command, request_key(request))
        assert isinstance(result, BootstrapResult)
        return result

    @router.post(
        "/api/v1/identity-bridge/token",
        openapi_extra={
            "parameters": [
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": True,
                    "schema": {"type": "string", "minLength": 1, "maxLength": 128},
                    "description": "Reuse exactly this key and body after a lost response.",
                }
            ]
        },
    )
    async def exchange(request: Request, response: Response, command: TokenRequest) -> TokenResult:
        token = bearer(request)
        response.headers["Cache-Control"] = "no-store"
        result = await bridge.process(token, command, request_key(request))
        assert isinstance(result, TokenResult)
        return result

    @router.post("/api/v1/identity-bridge/events")
    async def events(request: Request, response: Response, command: SyncEvent) -> EventResult:
        response.headers["Cache-Control"] = "no-store"
        return await synchronize(bridge, bearer(request), command)

    @router.get("/api/v1/me/connections")
    async def own_connections(
        request: Request, response: Response, page: Annotated[HTTPPageRequest, Query()]
    ) -> ConnectionPage:
        principal, member = await runtime.actor(request)
        response.headers["Cache-Control"] = "no-store"
        result = await bridge.management.list_page(
            principal, member.org_id, "connections", page, own=True
        )
        assert isinstance(result, ConnectionPage)
        return result

    @router.get("/api/v1/identity-integrations/{integration_id}/connections")
    async def connections(
        request: Request,
        response: Response,
        integration_id: UUID,
        page: Annotated[HTTPPageRequest, Query()],
    ) -> ConnectionPage:
        access = await admin(request)
        response.headers["Cache-Control"] = "no-store"
        result = await bridge.management.list_page(
            access.principal, access.org_id, "connections", page, integration_id=integration_id
        )
        assert isinstance(result, ConnectionPage)
        return result

    @router.post("/api/v1/me/connections/{connection_id}/revoke")
    async def revoke_own(
        request: Request, response: Response, connection_id: UUID, command: RevisionCommand
    ) -> ConnectionView:
        principal, member = await runtime.actor(request, mutating=True)
        response.headers["Cache-Control"] = "no-store"
        return await bridge.management.revoke(principal, member.org_id, connection_id, command)

    @router.post("/api/v1/identity-connections/{connection_id}/revoke")
    async def revoke(
        request: Request, response: Response, connection_id: UUID, command: RevisionCommand
    ) -> ConnectionView:
        access = await admin(request, mutating=True)
        response.headers["Cache-Control"] = "no-store"
        return await bridge.management.revoke(
            access.principal, access.org_id, connection_id, command, own=False
        )

    return router
