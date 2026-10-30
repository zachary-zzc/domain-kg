"""Typed catalog HTTP wrappers; policy and lifecycle stay in shared services."""

from typing import Annotated, Any, Literal, Self
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import Field, model_validator

from domain_kg.catalog.models import (
    BackendCapabilities,
    CreateKG,
    KGReceipt,
    RegisterBackend,
    StorageProfile,
)
from domain_kg.catalog.service import CatalogService
from domain_kg.identity.models import AccessContext, Action
from domain_kg.identity.routes import HTTPPageRequest, IdentityRuntime
from domain_kg.models import FrozenModel, IdempotencyKey, NonNegative, PageRequest


class BackendRegistration(RegisterBackend):
    idempotency_key: IdempotencyKey


class ProfileRegistration(StorageProfile):
    idempotency_key: IdempotencyKey


class KGRevisionCommand(FrozenModel):
    expected_revision: NonNegative
    idempotency_key: IdempotencyKey


class KGStateChange(KGRevisionCommand):
    state: Literal["ready", "read_only", "archived"] | None = None
    name: Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")] | None = None

    @model_validator(mode="after")
    def change_required(self) -> Self:
        if self.state is None and self.name is None:
            raise ValueError("provide a name or lifecycle state")
        return self


class CatalogQuery(HTTPPageRequest):
    lifecycle: (
        Literal["provisioning", "ready", "read_only", "archived", "error", "deleted"] | None
    ) = None


def catalog_router(identity: IdentityRuntime, service: CatalogService) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    async def access(
        request: Request, action: Action, kg_id: UUID | None = None, *, mutate: bool = False
    ) -> AccessContext:
        principal, member = await identity.actor(request, mutating=mutate)
        return await service.policy.resolve_access(
            principal, [kg_id] if kg_id else [], action, org_id=member.org_id
        )

    @router.get("/kgs")
    async def list_kgs(request: Request, query: Annotated[CatalogQuery, Query()]) -> dict[str, Any]:
        context = await access(request, Action.LIST_KGS)
        return await service.list_kgs(
            context, PageRequest(limit=query.limit, cursor=query.cursor), lifecycle=query.lifecycle
        )

    @router.post("/kgs")
    async def create_kg(request: Request, command: CreateKG) -> KGReceipt:
        return await service.create_kg(
            await access(request, Action.CREATE_KG, mutate=True), command
        )

    @router.get("/kgs/{kg_id}")
    async def get_kg(request: Request, kg_id: UUID) -> dict[str, Any]:
        return await service.get_kg(await access(request, Action.READ, kg_id), kg_id)

    @router.patch("/kgs/{kg_id}")
    async def change_kg(request: Request, kg_id: UUID, command: KGStateChange) -> KGReceipt:
        context = await access(request, Action.CONFIGURE, kg_id, mutate=True)
        return await service.change_state(
            context,
            kg_id,
            command.state,
            expected_revision=command.expected_revision,
            idempotency_key=command.idempotency_key,
            name=command.name,
        )

    @router.delete("/kgs/{kg_id}")
    async def delete_kg(request: Request, kg_id: UUID, command: KGRevisionCommand) -> KGReceipt:
        return await service.delete_kg(
            await access(request, Action.CONFIGURE, kg_id, mutate=True),
            kg_id,
            expected_revision=command.expected_revision,
            idempotency_key=command.idempotency_key,
        )

    @router.post("/kgs/{kg_id}/restore")
    async def restore_kg(request: Request, kg_id: UUID, command: KGRevisionCommand) -> KGReceipt:
        return await service.restore_kg(
            await access(request, Action.CONFIGURE, kg_id, mutate=True),
            kg_id,
            expected_revision=command.expected_revision,
            idempotency_key=command.idempotency_key,
        )

    @router.get("/kgs/{kg_id}/health")
    async def health(request: Request, kg_id: UUID) -> dict[str, str]:
        return await service.health(await access(request, Action.READ, kg_id), kg_id)

    @router.get("/backends")
    async def list_backends(
        request: Request, query: Annotated[HTTPPageRequest, Query()]
    ) -> dict[str, Any]:
        return await service.registry.inventory(
            await access(request, Action.REGISTER_BACKEND),
            "backends",
            PageRequest(limit=query.limit, cursor=query.cursor),
        )

    @router.get("/backends/approved-references")
    async def list_approved(
        request: Request, query: Annotated[HTTPPageRequest, Query()]
    ) -> dict[str, Any]:
        return await service.registry.inventory(
            await access(request, Action.REGISTER_BACKEND),
            "approved",
            PageRequest(limit=query.limit, cursor=query.cursor),
        )

    @router.post("/backends")
    async def register_backend(
        request: Request, command: BackendRegistration
    ) -> BackendCapabilities:
        context = await access(request, Action.REGISTER_BACKEND, mutate=True)
        return await service.registry.register(
            context,
            RegisterBackend(kind=command.kind, secret_ref=command.secret_ref),
            idempotency_key=command.idempotency_key,
        )

    @router.post("/backends/{backend_id}/validate")
    async def validate_backend(request: Request, backend_id: UUID) -> BackendCapabilities:
        context = await access(request, Action.REGISTER_BACKEND, mutate=True)
        records = await service.registry.list_backends(context)
        target = next((record for record in records if record.backend_id == backend_id), None)
        if target is None:
            from domain_kg.errors import inaccessible_reference

            raise inaccessible_reference()
        return await service.registry.probe(
            backend_id, await service.registry.connection(backend_id, target.kind)
        )

    @router.get("/storage-profiles")
    async def profiles(
        request: Request, query: Annotated[HTTPPageRequest, Query()]
    ) -> dict[str, Any]:
        return await service.registry.inventory(
            await access(request, Action.LIST_KGS),
            "profiles",
            PageRequest(limit=query.limit, cursor=query.cursor),
        )

    @router.post("/storage-profiles")
    async def register_profile(request: Request, command: ProfileRegistration) -> StorageProfile:
        context = await access(request, Action.REGISTER_BACKEND, mutate=True)
        profile = StorageProfile.model_validate(command.model_dump(exclude={"idempotency_key"}))
        return await service.registry.register_profile(
            context, profile, idempotency_key=command.idempotency_key
        )

    return router
