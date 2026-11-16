"""API adapter for the production source-pool and semantic reranking implementation."""

from pydantic import ValidationError

from domain_kg.errors import DomainError
from domain_kg.providers.budget import extraction_request_fits
from domain_kg.providers.rerank import (
    RANKING_INSTRUCTIONS,
    Ranking,
    ranked_sources,
    ranking_payload,
)
from domain_kg.retrieval.fusion import fuse_evidence, recall_pool, select_evidence
from eval.kg_quality_api import CloudCalls, chat_request, cloud_client
from eval.kg_quality_build import profile_for
from eval.source_role_eval import digest
from eval.source_role_run import write_new


class SourceMerger:
    def __init__(self, root, key):
        self.root = root
        self.calls = CloudCalls(root / "merge-calls", key)

    async def __call__(self, query, branches, model, *, namespace):
        candidates = recall_pool(branches)
        payload = chat_request(
            model, RANKING_INSTRUCTIONS, ranking_payload(query, candidates), 1024
        )
        if not candidates:
            return [], {"candidate_count": 0}
        profile = profile_for(model, namespace).model_copy(
            update={"max_completion_tokens": 1024, "extraction_context_tokens": 131072}
        )
        if not extraction_request_fits(payload, profile):
            raise ValueError("Complete source pool exceeds the frozen context bound")
        async with cloud_client() as client:
            result = await self.calls.chat(client, payload)
        error = result["error"]
        ranked = []
        if not error:
            try:
                preferred = ranked_sources(
                    candidates, Ranking.model_validate_json(result["content"])
                )
                ranked = fuse_evidence({**branches, "rerank": preferred})
            except (ValidationError, DomainError):
                error = "invalid_ranking_protocol"
        selected, truncated = select_evidence(ranked, top_k=12, max_chars=14000)
        if any(
            s.text != next(c.text for c in candidates if c.chunk_id == s.chunk_id) for s in selected
        ):
            raise ValueError("Source-only experiment must preserve complete passages")
        trace = {
            "error": error,
            "request_sha256": digest(payload),
            "candidate_count": len(candidates),
            "context_limited": truncated,
            "candidates": [str(c.chunk_id) for c in candidates],
            "ranked": [str(c.chunk_id) for c in ranked],
            "selected": [str(c.chunk_id) for c in selected],
        }
        path = self.root / "merge-traces" / (digest(payload) + ".json")
        if not path.exists():
            write_new(path, trace)
        return selected, trace
