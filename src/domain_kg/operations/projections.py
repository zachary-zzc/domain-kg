"""Owner readiness is measured against accepted receipts, never inferred from a job stage."""

from typing import Literal
from uuid import UUID

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.models import JobReceipt
from domain_kg.jobs.queue import JobQueue
from domain_kg.models import FrozenModel, IdempotencyKey, NonNegative
from domain_kg.retrieval.evidence import SealedEvidence
from domain_kg.retrieval.models import SearchRequest
from domain_kg.revisions.repair import RepairService


class ProjectionStatus(FrozenModel):
    kg_id: UUID
    published_revision: NonNegative
    generation: UUID
    state: Literal["ready", "repair_required"]
    source_count: NonNegative
    curation_count: NonNegative
    graph_ready: bool
    vector_ready: bool
    warnings: tuple[ErrorCode, ...] = ()


class RepairCommand(FrozenModel):
    material_id: UUID
    idempotency_key: IdempotencyKey


class ProjectionManagement:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.graph = queue, GraphService(queue)

    async def status(self, access: AccessContext, kg: UUID) -> ProjectionStatus:
        current = await self.queue.policy.revalidate(access, action=Action.CONFIGURE)
        scope = await self.graph.resolver.resolve(
            current, SearchRequest(kg_ids=(kg,), query="projection readiness"), kg
        )
        warnings = []
        graph_ready = vector_ready = True
        try:
            await self.graph.repository.open(scope)
        except DomainError as error:
            if error.code not in (ErrorCode.BACKEND_UNAVAILABLE, ErrorCode.PROJECTION_INCONSISTENT):
                raise
            graph_ready = False
            warnings.append(error.code)
        try:
            await SealedEvidence(self.queue).load(scope, "vector")
        except DomainError as error:
            if error.code not in (ErrorCode.BACKEND_UNAVAILABLE, ErrorCode.PROJECTION_INCONSISTENT):
                raise
            vector_ready = False
            warnings.append(error.code)
        await self.graph.resolver.revalidate(scope)
        await self.queue.policy.revalidate(current, action=Action.CONFIGURE)
        return ProjectionStatus(
            kg_id=kg,
            published_revision=scope.view.revision,
            generation=scope.view.generation,
            state="ready" if graph_ready and vector_ready else "repair_required",
            source_count=len(scope.visible_sources),
            curation_count=len(scope.visible_curations),
            graph_ready=graph_ready,
            vector_ready=vector_ready,
            warnings=tuple(dict.fromkeys(warnings)),
        )

    async def repair(
        self, access: AccessContext, kg: UUID, material: UUID, *, idempotency_key: str
    ) -> JobReceipt:
        current = await self.queue.policy.revalidate(access, action=Action.CONFIGURE)
        return await RepairService(self.queue).request(
            current, kg, material, idempotency_key=idempotency_key
        )
