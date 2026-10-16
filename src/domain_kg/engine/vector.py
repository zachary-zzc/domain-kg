"""Request-bound LightRAG chunk-vector reads through the accepted source branch."""

from typing import Any

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.retrieval.models import SearchRequest
from domain_kg.retrieval.vector import VectorRetrievalBranch
from domain_kg.revisions.models import ReadScope


class ScopedLightRAGVector:
    cosine_better_than_threshold = -1.0

    def __init__(self, branch: VectorRetrievalBranch, scope: ReadScope) -> None:
        self.branch, self.scope = branch, scope

    async def query(
        self, query: str, top_k: int, query_embedding: list[float] | None = None
    ) -> list[dict[str, Any]]:
        if query_embedding is not None:
            raise DomainError(
                ErrorCode.VERSION_CONFLICT,
                "A precomputed query vector requires a verified embedding-space identity.",
            )
        request = SearchRequest(
            kg_ids=(self.scope.view.kg_id,),
            query=query,
            mode="vector",
            top_k=top_k,
            filters=self.scope.filters,
        )
        candidates = await self.branch.retrieve(self.scope, request)
        return [
            {
                "id": str(item.chunk_id),
                "content": item.text,
                "distance": item.score,
                "file_path": (
                    f"kg:{item.kg_id}/material:{item.material_id}/"
                    f"version:{item.version_id}/chunk:{item.chunk_id}"
                ),
            }
            for item in candidates[:top_k]
        ]
