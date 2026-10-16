"""HTTP wrappers share application services and policy with the MCP tools."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from pydantic import Field

from domain_kg.citations.models import Citation
from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.views import NodeView, SubgraphView
from domain_kg.identity.grant_models import GrantPage, MemberRoleChange, PolicyReceipt
from domain_kg.identity.material_grants import MaterialACLChange, MaterialACLView
from domain_kg.identity.models import AccessContext, Action
from domain_kg.identity.routes import HTTPPageRequest
from domain_kg.imports.models import ImportPlan
from domain_kg.imports.requests import ImportRequest
from domain_kg.jobs.models import JobReceipt
from domain_kg.materials.inspection_models import MaterialDetail, MaterialPage
from domain_kg.materials.models import (
    MaterialMetadataReceipt,
    RemovalReceipt,
    RemoveMaterial,
    RestorationReceipt,
    RestoreMaterial,
    UpdateMaterialMetadata,
)
from domain_kg.materials.purge_models import PurgeMaterial, PurgeReceipt
from domain_kg.models import FrozenModel, IdempotencyKey, PageRequest
from domain_kg.operations.projections import ProjectionStatus, RepairCommand
from domain_kg.retrieval.models import SearchFilters, SearchRequest, SearchResponse, SubgraphRequest
from domain_kg.revisions.models import (
    CurationCommand,
    HistoryPage,
    HistoryRequest,
    NodeUpdate,
    RelationUpdate,
    RevisionReceipt,
    RollbackRevision,
)
from domain_kg.transport.models import CurationResult
from domain_kg.transport.runtime import ServiceRuntime


class MaterialQuery(SearchFilters):
    limit: Annotated[int, Field(ge=1, le=100)] = 25
    cursor: str | None = None


class HistoryQuery(HTTPPageRequest):
    target_id: UUID
    kind: Literal["node", "relation"] = "node"
    include_suppressed: bool = False


class JobControl(FrozenModel):
    idempotency_key: IdempotencyKey


def matching(*pairs: tuple[UUID, UUID]) -> None:
    if any(path != body for path, body in pairs):
        raise DomainError(ErrorCode.VALIDATION_ERROR, "Path and command identities must match.")


def service_router(runtime: ServiceRuntime) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    async def access(
        request: Request,
        kg_ids: tuple[UUID, ...],
        action: Action,
        *,
        mutate: bool = False,
    ) -> AccessContext:
        principal, member = await runtime.identity.actor(request, mutating=mutate)
        return await runtime.access(principal, kg_ids, action, member.org_id)

    @router.post("/search")
    async def search(request: Request, command: SearchRequest) -> SearchResponse:
        current = await access(request, command.kg_ids, Action.SEARCH)
        return await runtime.search.search(current, command)

    @router.post("/kgs/{kg_id}/imports")
    async def import_kg(
        request: Request, response: Response, kg_id: UUID, command: ImportRequest
    ) -> ImportPlan | JobReceipt:
        source = command.source if command.operation == "plan" else command.plan.source
        matching((kg_id, source.kg_id))
        current = await access(request, (kg_id,), Action.INGEST, mutate=True)
        if command.operation == "plan":
            return await runtime.imports.plan(current, command.source)
        matching((kg_id, command.plan.kg_id))
        receipt = await runtime.imports.execute(current, command.plan)
        response.status_code = 202
        return receipt

    @router.get("/citations/{citation_id}")
    async def citation(
        request: Request,
        citation_id: UUID,
        kg_id: UUID,
        material_id: UUID | None = None,
        version_id: UUID | None = None,
        include_unverified: bool = False,
    ) -> Citation:
        if version_id is not None and material_id is None:
            raise DomainError(ErrorCode.VALIDATION_ERROR, "A version requires its material ID.")
        return await runtime.citations.resolve(
            await access(request, (kg_id,), Action.READ),
            kg_id,
            citation_id,
            material_id=material_id,
            version_id=version_id,
            include_unverified=include_unverified,
        )

    @router.get("/kgs/{kg_id}/nodes/{entity_id}")
    async def node(
        request: Request,
        kg_id: UUID,
        entity_id: UUID,
        filters: Annotated[SearchFilters, Query()],
    ) -> NodeView:
        current = await access(request, (kg_id,), Action.READ)
        return await runtime.graph.get_node(current, kg_id, entity_id, filters)

    @router.post("/kgs/{kg_id}/subgraph")
    async def subgraph(request: Request, kg_id: UUID, command: SubgraphRequest) -> SubgraphView:
        matching((kg_id, command.kg_id))
        return await runtime.graph.subgraph(await access(request, (kg_id,), Action.READ), command)

    @router.patch("/kgs/{kg_id}/nodes/{entity_id}", status_code=202)
    async def update_node(
        request: Request, kg_id: UUID, entity_id: UUID, command: NodeUpdate
    ) -> RevisionReceipt:
        matching((kg_id, command.kg_id), (entity_id, command.entity_id))
        return await runtime.revisions.update_node(
            await access(request, (kg_id,), Action.CURATE, mutate=True), command
        )

    @router.patch("/kgs/{kg_id}/relations/{assertion_id}", status_code=202)
    async def update_relation(
        request: Request, kg_id: UUID, assertion_id: UUID, command: RelationUpdate
    ) -> RevisionReceipt:
        matching((kg_id, command.kg_id), (assertion_id, command.assertion_id))
        return await runtime.revisions.update_relation(
            await access(request, (kg_id,), Action.CURATE, mutate=True), command
        )

    @router.post("/kgs/{kg_id}/nodes/{entity_id}/curate")
    async def curate(
        request: Request, kg_id: UUID, entity_id: UUID, command: CurationCommand
    ) -> CurationResult:
        matching((kg_id, command.kg_id), (entity_id, command.entity_id))
        return CurationResult(
            result=await runtime.revisions.curate_node(
                await access(request, (kg_id,), Action.CURATE, mutate=True), command
            )
        )

    @router.get("/kgs/{kg_id}/revisions")
    async def history(
        request: Request, kg_id: UUID, query: Annotated[HistoryQuery, Query()]
    ) -> HistoryPage:
        current = await access(
            request, (kg_id,), Action.CURATE if query.include_suppressed else Action.READ
        )
        return await runtime.revisions.get_revisions(
            current, HistoryRequest(kg_id=kg_id, **query.model_dump())
        )

    @router.post("/kgs/{kg_id}/revisions/{revision_id}/rollback")
    async def rollback(
        request: Request, kg_id: UUID, revision_id: UUID, command: RollbackRevision
    ) -> CurationResult:
        matching((kg_id, command.kg_id), (revision_id, command.revision_id))
        return CurationResult(
            result=await runtime.revisions.rollback(
                await access(request, (kg_id,), Action.CURATE, mutate=True), command
            )
        )

    @router.get("/kgs/{kg_id}/materials")
    async def materials(
        request: Request, kg_id: UUID, query: Annotated[MaterialQuery, Query()]
    ) -> MaterialPage:
        current = await access(request, (kg_id,), Action.READ)
        return await runtime.inspection.list(
            current,
            kg_id,
            PageRequest(limit=query.limit, cursor=query.cursor),
            SearchFilters.model_validate(query.model_dump(exclude={"limit", "cursor"})),
        )

    @router.get("/kgs/{kg_id}/materials/{material_id}")
    async def material(
        request: Request,
        kg_id: UUID,
        material_id: UUID,
        version_id: UUID | None = None,
        include_unverified: bool = False,
    ) -> MaterialDetail:
        current = await access(request, (kg_id,), Action.READ)
        return await runtime.inspection.get(
            current, kg_id, material_id, version_id, include_unverified=include_unverified
        )

    @router.patch("/kgs/{kg_id}/materials/{material_id}")
    async def metadata(
        request: Request, kg_id: UUID, material_id: UUID, command: UpdateMaterialMetadata
    ) -> MaterialMetadataReceipt:
        matching((kg_id, command.kg_id), (material_id, command.material_id))
        return await runtime.lifecycle.update_metadata(
            await access(request, (kg_id,), Action.REPLACE, mutate=True), command
        )

    @router.post("/kgs/{kg_id}/materials/{material_id}/remove")
    async def remove(
        request: Request, kg_id: UUID, material_id: UUID, command: RemoveMaterial
    ) -> RemovalReceipt:
        matching((kg_id, command.kg_id), (material_id, command.material_id))
        return await runtime.lifecycle.remove(
            await access(request, (kg_id,), Action.REMOVE, mutate=True), command
        )

    @router.post("/kgs/{kg_id}/materials/{material_id}/restore")
    async def restore(
        request: Request, kg_id: UUID, material_id: UUID, command: RestoreMaterial
    ) -> RestorationReceipt:
        matching((kg_id, command.kg_id), (material_id, command.material_id))
        return await runtime.lifecycle.restore(
            await access(request, (kg_id,), Action.REPLACE, mutate=True), command
        )

    @router.post("/kgs/{kg_id}/materials/{material_id}/purge", status_code=202)
    async def purge(
        request: Request, kg_id: UUID, material_id: UUID, command: PurgeMaterial
    ) -> PurgeReceipt:
        matching((kg_id, command.kg_id), (material_id, command.material_id))
        return await runtime.lifecycle.purge(
            await access(request, (kg_id,), Action.PURGE, mutate=True), command
        )

    @router.get("/kgs/{kg_id}/jobs/{job_id}")
    async def job(request: Request, kg_id: UUID, job_id: UUID) -> JobReceipt:
        return await runtime.queue.get(await access(request, (kg_id,), Action.READ), job_id)

    @router.post("/kgs/{kg_id}/jobs/{job_id}/cancel")
    async def cancel(
        request: Request, kg_id: UUID, job_id: UUID, command: JobControl
    ) -> JobReceipt:
        current = await access(request, (kg_id,), Action.READ, mutate=True)
        return await runtime.queue.cancel(current, job_id, idempotency_key=command.idempotency_key)

    @router.post("/kgs/{kg_id}/jobs/{job_id}/retry", status_code=202)
    async def retry(request: Request, kg_id: UUID, job_id: UUID, command: JobControl) -> JobReceipt:
        current = await access(request, (kg_id,), Action.READ, mutate=True)
        return await runtime.queue.retry(current, job_id, idempotency_key=command.idempotency_key)

    @router.get("/kgs/{kg_id}/members")
    async def members(
        request: Request, kg_id: UUID, page: Annotated[HTTPPageRequest, Query()]
    ) -> GrantPage:
        current = await access(request, (kg_id,), Action.MANAGE_GRANTS)
        return await runtime.grants.list(
            current, kg_id, PageRequest.model_validate(page.model_dump())
        )

    @router.get("/kgs/{kg_id}/materials/{material_id}/acl")
    async def material_acl(request: Request, kg_id: UUID, material_id: UUID) -> MaterialACLView:
        return await runtime.material_grants.get(
            await access(request, (kg_id,), Action.MANAGE_GRANTS), kg_id, material_id
        )

    @router.put("/kgs/{kg_id}/materials/{material_id}/acl")
    async def set_material_acl(
        request: Request, kg_id: UUID, material_id: UUID, command: MaterialACLChange
    ) -> PolicyReceipt:
        matching((kg_id, command.kg_id), (material_id, command.material_id))
        return await runtime.material_grants.set(
            await access(request, (kg_id,), Action.MANAGE_GRANTS, mutate=True), command
        )

    @router.get("/kgs/{kg_id}/projections")
    async def projections(request: Request, kg_id: UUID) -> ProjectionStatus:
        return await runtime.projections.status(
            await access(request, (kg_id,), Action.CONFIGURE), kg_id
        )

    @router.post("/kgs/{kg_id}/repair", status_code=202)
    async def repair(request: Request, kg_id: UUID, command: RepairCommand) -> JobReceipt:
        return await runtime.projections.repair(
            await access(request, (kg_id,), Action.CONFIGURE, mutate=True),
            kg_id,
            command.material_id,
            idempotency_key=command.idempotency_key,
        )

    @router.put("/kgs/{kg_id}/members/{subject_id}")
    @router.delete("/kgs/{kg_id}/members/{subject_id}")
    async def role(
        request: Request, kg_id: UUID, subject_id: UUID, command: MemberRoleChange
    ) -> PolicyReceipt:
        matching((kg_id, command.kg_id), (subject_id, command.subject_id))
        if (request.method == "DELETE") != (command.role is None):
            raise DomainError(
                ErrorCode.VALIDATION_ERROR, "Use DELETE to revoke or PUT to assign a role."
            )
        return await runtime.grants.set(
            await access(request, (kg_id,), Action.MANAGE_GRANTS, mutate=True), command
        )

    return router
