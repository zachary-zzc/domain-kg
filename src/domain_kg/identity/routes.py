"""External OIDC login and organization APIs using the same identity authority."""

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from urllib.parse import urlencode
from uuid import UUID

import httpx
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from mcp.server.auth.provider import AccessToken, TokenVerifier
from pydantic import Field

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Group, Member, Principal
from domain_kg.identity.oidc import OIDCVerifier
from domain_kg.identity.repository import IdentityRepository
from domain_kg.identity.sessions import SessionStore, token_hash
from domain_kg.models import FrozenModel, IdempotencyKey, NonNegative, PageRequest, PreservedText
from domain_kg.settings import Settings


class AdmitMember(FrozenModel):
    issuer: PreservedText
    subject: Annotated[PreservedText, Field(min_length=1)]
    role: Literal["admin", "member"] = "member"
    idempotency_key: IdempotencyKey


class AdmitService(AdmitMember):
    kg_ids: Annotated[tuple[UUID, ...], Field(min_length=1)]
    scopes: Annotated[tuple[str, ...], Field(min_length=1)]


class ChangeMember(FrozenModel):
    expected_revision: NonNegative
    role: Literal["admin", "member"] | None = None
    suspended: bool | None = None
    idempotency_key: IdempotencyKey


class CreateGroup(FrozenModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    idempotency_key: IdempotencyKey


class GroupMemberChange(FrozenModel):
    idempotency_key: IdempotencyKey


class HTTPPageRequest(PageRequest):
    # Query strings are converted at the transport boundary before domain use.
    limit: Annotated[int, Field(ge=1, le=100)] = 25


class IdentityRuntime(TokenVerifier):
    def __init__(
        self, settings: Settings, repository: IdentityRepository, client: httpx.AsyncClient
    ) -> None:
        self.settings, self.repository, self.client = settings, repository, client
        self.verifier = OIDCVerifier(
            settings.oidc_issuer, f"{str(settings.public_url).rstrip('/')}/mcp", client
        )
        self.sessions = SessionStore(repository.database, repository)
        self.origin = str(settings.public_url).rstrip("/")
        self.secure = self.origin.startswith("https:")

    async def initialize(self) -> None:
        await self.repository.initialize()
        await self.sessions.initialize()

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            principal = await self.verifier.authenticate_bearer(token)
            member = await self.repository.require_member(principal)
            scopes = principal.scopes
            if member.kind == "service":
                scopes &= member.scopes
            if "domain_kg:read" not in scopes:
                return None
            claims = await self.verifier.verify_claims(token)
            return AccessToken(
                token=token,
                client_id=str(claims.get("client_id", claims.get("azp", principal.subject))),
                scopes=sorted(scopes),
                expires_at=int(claims["exp"]),
                resource=self.verifier.audience,
                subject=principal.subject,
                claims={"iss": principal.issuer, "sub": principal.subject},
            )
        except DomainError:
            return None

    async def actor(self, request: Request, *, mutating: bool = False) -> tuple[Principal, Member]:
        header = request.headers.get("authorization", "")
        if header:
            scheme, _, token = header.partition(" ")
            if scheme.lower() != "bearer" or not token:
                raise DomainError(ErrorCode.UNAUTHENTICATED, "A Bearer credential is required.")
            principal = await self.verifier.authenticate_bearer(token)
            selected = request.headers.get("x-domain-kg-organization")
            try:
                org = UUID(selected) if selected is not None else None
            except ValueError as exc:
                raise DomainError(
                    ErrorCode.VALIDATION_ERROR, "Invalid organization selector."
                ) from exc
        else:
            session = request.cookies.get("domain_kg_session", "")
            csrf = request.headers.get("x-csrf-token")
            if mutating and (not csrf or request.headers.get("origin") != self.origin):
                raise DomainError(ErrorCode.FORBIDDEN, "A same-origin CSRF token is required.")
            principal = await self.sessions.authenticate_session(
                session, csrf=csrf if mutating else None
            )
            org = await self.sessions.organization(session)
        member = await self.repository.require_member(principal, org)
        if member.kind == "service":
            if "domain_kg:read" not in (principal.scopes & member.scopes):
                raise DomainError(
                    ErrorCode.FORBIDDEN, "The service credential lacks its granted scope."
                )
            principal = Principal(
                issuer=principal.issuer,
                subject=principal.subject,
                scopes=principal.scopes & member.scopes,
                kind="service",
            )
        return principal, member


def identity_router(runtime: IdentityRuntime) -> APIRouter:
    router = APIRouter()

    @router.get("/auth/login")
    async def login(org_id: UUID | None = None) -> Response:
        metadata = await runtime.verifier.metadata()
        state, browser, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(4))
        await runtime.repository.database.oidc_states.insert_one(
            {
                "_id": token_hash(state),
                "browser_hash": token_hash(browser),
                "nonce_hash": token_hash(nonce),
                "verifier": verifier,
                "org_id": str(org_id) if org_id else None,
                "expires_at": datetime.now(UTC) + timedelta(minutes=5),
            }
        )
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        parameters = {
            "client_id": runtime.settings.oidc_client_id,
            "redirect_uri": f"{runtime.origin}/auth/callback",
            "response_type": "code",
            "scope": "openid",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        response = RedirectResponse(
            metadata["authorization_endpoint"] + "?" + urlencode(parameters), status_code=303
        )
        response.set_cookie(
            "domain_kg_login",
            browser,
            max_age=300,
            httponly=True,
            secure=runtime.secure,
            samesite="lax",
            path="/auth",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.get("/auth/callback")
    async def callback(
        request: Request,
        code: Annotated[str, Query(min_length=1, max_length=4096)],
        state: Annotated[str, Query(min_length=1, max_length=256)],
    ) -> Response:
        pending = await runtime.repository.database.oidc_states.find_one_and_delete(
            {
                "_id": token_hash(state),
                "browser_hash": token_hash(request.cookies.get("domain_kg_login", "")),
                "expires_at": {"$gt": datetime.now(UTC)},
            }
        )
        if pending is None:
            raise DomainError(ErrorCode.UNAUTHENTICATED, "The login attempt is invalid or expired.")
        metadata = await runtime.verifier.metadata()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": runtime.settings.oidc_client_id,
            "redirect_uri": f"{runtime.origin}/auth/callback",
            "code_verifier": pending["verifier"],
        }
        secret = runtime.settings.oidc_client_secret.get_secret_value()
        auth = (
            httpx.BasicAuth(runtime.settings.oidc_client_id, secret)
            if secret
            else httpx.USE_CLIENT_DEFAULT
        )
        try:
            result = await runtime.client.post(
                metadata["token_endpoint"], data=data, auth=auth, follow_redirects=False
            )
            result.raise_for_status()
            claims = await runtime.verifier.verify_claims(
                result.json()["id_token"], audience=runtime.settings.oidc_client_id
            )
            nonce = claims.get("nonce")
            if not isinstance(nonce, str) or not secrets.compare_digest(
                pending["nonce_hash"], token_hash(nonce)
            ):
                raise ValueError("nonce mismatch")
            audiences = claims["aud"]
            if (
                isinstance(audiences, list)
                and len(audiences) > 1
                and claims.get("azp") != runtime.settings.oidc_client_id
            ) or ("azp" in claims and claims["azp"] != runtime.settings.oidc_client_id):
                raise ValueError("authorized party mismatch")
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise DomainError(
                ErrorCode.UNAUTHENTICATED, "The provider login could not be verified."
            ) from exc
        principal = Principal(issuer=claims["iss"], subject=claims["sub"])
        member = await runtime.repository.require_member(
            principal, UUID(pending["org_id"]) if pending["org_id"] else None
        )
        ticket = await runtime.sessions.create(principal, member.org_id)
        response = RedirectResponse("/admin", status_code=303)
        response.set_cookie(
            "domain_kg_session",
            ticket.session_id,
            max_age=8 * 3600,
            httponly=True,
            secure=runtime.secure,
            samesite="lax",
            path="/",
        )
        response.set_cookie(
            "domain_kg_csrf",
            ticket.csrf,
            max_age=8 * 3600,
            secure=runtime.secure,
            samesite="strict",
            path="/",
        )
        response.delete_cookie("domain_kg_login", path="/auth")
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.post("/auth/logout")
    async def logout(request: Request) -> Response:
        await runtime.actor(request, mutating=True)
        await runtime.sessions.revoke(request.cookies.get("domain_kg_session", ""))
        response = JSONResponse({"status": "ok"})
        response.delete_cookie("domain_kg_session", path="/")
        response.delete_cookie("domain_kg_csrf", path="/")
        return response

    @router.get("/api/v1/me")
    async def me(request: Request) -> dict[str, Any]:
        principal, member = await runtime.actor(request)
        return {
            "principal": principal.model_dump(mode="json"),
            "member": member.model_dump(mode="json"),
        }

    @router.get("/api/v1/organizations/current")
    async def organization(request: Request) -> dict[str, Any]:
        _, member = await runtime.actor(request)
        raw = await runtime.repository.database.organizations.find_one({"_id": str(member.org_id)})
        if raw is None:
            raise DomainError(ErrorCode.FORBIDDEN, "Organization access is unavailable.")
        return {"id": raw["_id"], "name": raw["name"], "policy_revision": raw["policy_revision"]}

    @router.get("/api/v1/members")
    async def members(
        request: Request, page: Annotated[HTTPPageRequest, Query()]
    ) -> dict[str, Any]:
        _, actor = await runtime.actor(request)
        return await runtime.repository.list_page(actor, "members", page)

    @router.post("/api/v1/members")
    async def admit(request: Request, command: AdmitMember) -> Member:
        _, actor = await runtime.actor(request, mutating=True)
        return await runtime.repository.admit(
            actor,
            Principal(issuer=command.issuer, subject=command.subject),
            role=command.role,
            idempotency_key=command.idempotency_key,
        )

    @router.patch("/api/v1/members/{member_id}")
    async def change(request: Request, member_id: UUID, command: ChangeMember) -> Member:
        _, actor = await runtime.actor(request, mutating=True)
        return await runtime.repository.change_member(
            actor,
            member_id,
            expected_revision=command.expected_revision,
            role=command.role,
            suspended=command.suspended,
            idempotency_key=command.idempotency_key,
        )

    @router.get("/api/v1/groups")
    async def groups(request: Request, page: Annotated[HTTPPageRequest, Query()]) -> dict[str, Any]:
        _, actor = await runtime.actor(request)
        return await runtime.repository.list_page(actor, "groups", page)

    @router.post("/api/v1/groups")
    async def group(request: Request, command: CreateGroup) -> Group:
        _, actor = await runtime.actor(request, mutating=True)
        return await runtime.repository.create_group(
            actor, command.name, idempotency_key=command.idempotency_key
        )

    @router.put("/api/v1/groups/{group_id}/members/{member_id}")
    @router.delete("/api/v1/groups/{group_id}/members/{member_id}")
    async def group_member(
        request: Request, group_id: UUID, member_id: UUID, command: GroupMemberChange
    ) -> Group:
        _, actor = await runtime.actor(request, mutating=True)
        return await runtime.repository.set_group_member(
            actor,
            group_id,
            member_id,
            present=request.method == "PUT",
            idempotency_key=command.idempotency_key,
        )

    @router.get("/api/v1/service-identities")
    async def services(
        request: Request, page: Annotated[HTTPPageRequest, Query()]
    ) -> dict[str, Any]:
        _, actor = await runtime.actor(request)
        return await runtime.repository.list_page(actor, "services", page)

    @router.post("/api/v1/service-identities")
    async def service(request: Request, command: AdmitService) -> Member:
        _, actor = await runtime.actor(request, mutating=True)
        return await runtime.repository.admit(
            actor,
            Principal(issuer=command.issuer, subject=command.subject),
            role=command.role,
            kind="service",
            kg_ids=command.kg_ids,
            scopes=command.scopes,
            idempotency_key=command.idempotency_key,
        )

    @router.post("/api/v1/service-identities/{member_id}/revoke")
    async def revoke(request: Request, member_id: UUID, command: ChangeMember) -> Member:
        _, actor = await runtime.actor(request, mutating=True)
        target = await runtime.repository.database.members.find_one(
            {"_id": str(member_id), "org_id": str(actor.org_id), "kind": "service"}
        )
        if target is None:
            raise DomainError(ErrorCode.RESOURCE_NOT_ACCESSIBLE, "The identity is not accessible.")
        return await runtime.repository.change_member(
            actor,
            member_id,
            expected_revision=command.expected_revision,
            suspended=True,
            idempotency_key=command.idempotency_key,
        )

    return router
