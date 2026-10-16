"""Filtered Qdrant recall; no generative LLM, reranker or graph expansion."""

from qdrant_client import models

from domain_kg.engine.scope import ScopePredicates
from domain_kg.errors import BranchUnavailable, DomainError, ErrorCode
from domain_kg.jobs.queue import JobQueue
from domain_kg.models import Namespace
from domain_kg.providers.service import ProviderService
from domain_kg.retrieval.evidence import SealedEvidence
from domain_kg.retrieval.models import EvidenceCandidate, SearchRequest
from domain_kg.revisions.models import ReadScope
from domain_kg.storage.neo4j import inconsistent


class VectorRetrievalBranch:
    def __init__(self, queue: JobQueue, providers: ProviderService) -> None:
        self.evidence, self.providers = SealedEvidence(queue), providers
        self.resolver = self.evidence.resolver

    async def retrieve(
        self, scope: ReadScope, request: SearchRequest, *, exact: bool = False
    ) -> list[EvidenceCandidate]:
        request = SearchRequest.model_validate(request)
        if scope.view.kg_id not in request.kg_ids or scope.filters != request.filters:
            raise ValueError("vector request and immutable scope differ")
        data = await self.evidence.load(scope, "vector")
        chunks = data.chunks
        if not chunks:
            return []
        selected = await self.providers.selected(scope.access, scope.view.kg_id)
        space = selected.get("embedding_space_id")
        if space is None or set(data.spaces) != {space}:
            raise DomainError(
                ErrorCode.VERSION_CONFLICT, "The accepted embedding space requires migration."
            )
        profile, vectors = await self.providers.embeddings(
            scope.access,
            Namespace(org_id=scope.view.org_id, kg_id=scope.view.kg_id),
            [request.query],
        )
        if profile.space_id != space:
            raise DomainError(ErrorCode.VERSION_CONFLICT, "The query embedding space changed.")
        binding = await self.evidence.queue.router.for_vectors(scope.access, scope.view.kg_id)
        expected = {payload["projection_id"]: payload for payload in data.payloads}
        query_filter = models.Filter(
            must=[
                ScopePredicates(scope).vector_filter,
                models.HasIdCondition(has_id=list(chunks)),
                models.FieldCondition(
                    key="embedding_space_id", match=models.MatchValue(value=space)
                ),
                models.FieldCondition(key="kind", match=models.MatchValue(value="chunk")),
            ]
        )
        try:
            response = await binding.client.query_points(
                binding.collection,
                query=vectors[0],
                query_filter=query_filter,
                search_params=models.SearchParams(exact=exact),
                limit=50,
                with_payload=True,
            )
        except Exception as error:
            raise BranchUnavailable("Vector recall is unavailable.") from error
        for point in response.points:
            if str(point.id) not in chunks or point.payload != expected[str(point.id)]:
                raise inconsistent()
        points = sorted(response.points, key=lambda point: (-point.score, str(point.id)))
        result = [
            data.candidate(chunks[str(point.id)], "vector", point.score, rank)
            for rank, point in enumerate(points, 1)
        ]
        await self.resolver.revalidate(scope)
        return result
