"""Scoped source-vector seeds, bounded graph expansion, native mix and lexical recall."""

from domain_kg.engine.mix import native_mix
from domain_kg.retrieval.graph import GraphRecall, GraphRetrievalBranch
from domain_kg.retrieval.models import EvidenceCandidate, SearchRequest
from domain_kg.revisions.models import ReadScope


class DeepRetrievalBranch:
    def __init__(self, graph: GraphRetrievalBranch) -> None:
        self.graph = graph

    async def retrieve(
        self,
        scope: ReadScope,
        request: SearchRequest,
        vectors: list[EvidenceCandidate],
        *,
        keywords: tuple[str, ...] = (),
    ) -> GraphRecall:
        seeds = tuple(dict.fromkeys(entity for item in vectors for entity in item.entity_ids))
        recall = await self.graph.recall(scope, request, seed_ids=seeds, keywords=keywords)
        await native_mix(recall.graph, vectors)
        await self.graph.resolver.revalidate(scope)
        return recall
