"""Inspection and traversal use the same immutable eligibility as search."""

from uuid import UUID

from domain_kg.engine.scope import EligibilityResolver
from domain_kg.errors import inaccessible_reference
from domain_kg.graph.repository import GraphRepository
from domain_kg.graph.views import NodeView, SubgraphView
from domain_kg.graph.visibility_models import NodeFence
from domain_kg.identity.models import AccessContext
from domain_kg.jobs.queue import JobQueue
from domain_kg.retrieval.models import SearchFilters, SearchRequest, SubgraphRequest
from domain_kg.revisions.journal import PublicationJournal
from domain_kg.revisions.models import PublishedView, RetainedSelection


class AcceptedMetadata:
    """Branch verification follows eligibility, so private payloads are never read."""

    def __init__(self, queue: JobQueue) -> None:
        self.journal = PublicationJournal(queue)

    async def node_fences(
        self, access: AccessContext, kg_id: UUID, materials: tuple[UUID, ...]
    ) -> tuple[NodeFence, ...]:
        from domain_kg.graph.node_actions import NodeVisibilityService

        return await NodeVisibilityService(self.journal.queue).load(access, kg_id, materials)

    async def load(self, access: AccessContext, kg_id: UUID) -> PublishedView:
        return await self.journal.load_metadata(access, kg_id)

    async def retained(
        self, access: AccessContext, kg_id: UUID, material_id: UUID, version_id: UUID
    ) -> RetainedSelection:
        return await self.journal.retained(access, kg_id, material_id, version_id)


class GraphService:
    def __init__(self, queue: JobQueue) -> None:
        self.resolver = EligibilityResolver(queue.policy, AcceptedMetadata(queue))
        self.repository = GraphRepository(queue, self.resolver)

    async def get_node(
        self,
        access: AccessContext,
        kg_id: UUID,
        entity_id: UUID,
        filters: SearchFilters | None = None,
    ) -> NodeView:
        request = SearchRequest(
            kg_ids=(kg_id,), query=str(entity_id), mode="graph", filters=filters or SearchFilters()
        )
        scope = await self.resolver.resolve(access, request, kg_id)
        node = self.repository.node(await self.repository.open(scope), entity_id)
        await self.resolver.revalidate(scope)
        if node is None:
            raise inaccessible_reference()
        return node

    async def subgraph(self, access: AccessContext, request: SubgraphRequest) -> SubgraphView:
        request = SubgraphRequest.model_validate(request)
        scope = await self.resolver.resolve(
            access,
            SearchRequest(
                kg_ids=(request.kg_id,),
                query="graph",
                mode="graph",
                filters=request.filters,
                max_hops=request.max_hops,
                max_nodes=request.max_nodes,
                max_edges=request.max_edges,
            ),
            request.kg_id,
        )
        return await self.repository.traverse(scope, request)
