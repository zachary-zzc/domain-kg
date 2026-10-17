"""Model limits change source windows without losing original citation offsets."""

import pytest
from test_chunking import document
from test_provider_extraction import configured

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.schema import schema_for
from domain_kg.ingestion.chunking import chunk_document
from domain_kg.ingestion.models import ChunkConfig
from domain_kg.models import Namespace
from domain_kg.providers.budget import extraction_fits, fit_source_chunks, split_embedding_input


def test_windows_fit_both_models_and_preserve_unicode_source_offsets():
    source = document("细胞研究🔬 exact evidence. " * 140)
    base = chunk_document(source, ChunkConfig())
    profile, _ = configured(base[0])
    profile = profile.model_copy(
        update={
            "extraction_context_tokens": 4096,
            "max_completion_tokens": 512,
            "schema_in_context": False,
            "embedding_context_tokens": 1024,
        }
    )
    schema = schema_for(Namespace(org_id=source.blob.org_id, kg_id=source.blob.kg_id), "general")
    windows = fit_source_chunks(base, schema, profile)
    assert len(windows) > len(base)
    assert fit_source_chunks(base, schema, profile) == windows
    covered = set()
    for chunk in windows:
        assert extraction_fits(chunk, schema, profile)
        assert len(chunk.text.encode()) + 128 <= profile.embedding_context_tokens
        for span in chunk.spans:
            assert span.quote == source.blocks[0].text[span.start : span.end]
            covered.update(range(span.start, span.end))
    assert covered == set(range(len(source.blocks[0].text)))
    assert all(chunk.id not in {item.id for item in base} for chunk in windows)


def test_small_unchanged_chunks_keep_ids_and_impossible_budget_fails_before_calls():
    source = document("Small evidence.")
    base = chunk_document(source, ChunkConfig())
    profile, _ = configured(base[0])
    schema = schema_for(Namespace(org_id=source.blob.org_id, kg_id=source.blob.kg_id), "general")
    assert fit_source_chunks(base, schema, profile) == base
    profile = profile.model_copy(
        update={"extraction_context_tokens": 1024, "max_completion_tokens": 1024}
    )
    with pytest.raises(DomainError) as error:
        fit_source_chunks(base, schema, profile)
    assert error.value.code == ErrorCode.QUOTA_EXCEEDED


def test_embedding_windows_are_bounded_and_never_split_utf8_characters():
    text = "🔬细胞 evidence " * 100
    parts = split_embedding_input(text, context_tokens=256, prefix="Query: ")
    assert "".join(parts) == text and len(parts) > 1
    assert all(len(("Query: " + part).encode()) + 128 <= 256 for part in parts)


def test_visual_pdf_lines_are_packed_with_complete_coverage_under_small_model_budget():
    from uuid import uuid4

    from domain_kg.ingestion.models import SourceLocation, Span

    chunk = chunk_document(document("First line."), ChunkConfig())[0]
    spans = tuple(
        Span(
            location=SourceLocation(kind="pdf", page=2, block_id=uuid4()),
            start=0,
            end=len(text),
            quote=text,
        )
        for text in (f"Line {i}: exact 🔬 source text. " * 3 for i in range(20))
    )
    chunk = chunk.model_copy(update={"spans": spans, "text": "\n".join(s.quote for s in spans)})
    profile, _ = configured(chunk)
    profile = profile.model_copy(
        update={
            "extraction_format": "source_refs_v1",
            "extraction_context_tokens": 4096,
            "max_completion_tokens": 1024,
            "schema_in_context": False,
            "embedding_context_tokens": 2048,
        }
    )
    schema = schema_for(Namespace(org_id=chunk.org_id, kg_id=chunk.kg_id), "general")
    windows = fit_source_chunks([chunk], schema, profile)
    assert 1 < len(windows) < 10  # 20 individual visual lines must not become 20 requests.
    assert windows == fit_source_chunks([chunk], schema, profile)
    assert all(extraction_fits(w, schema, profile) for w in windows)
    assert all(len(w.text.encode()) + 128 <= 2048 for w in windows)
    for original in spans:
        covered = set()
        for window in windows:
            for part in window.spans:
                if part.location == original.location:
                    assert part.quote == original.quote[part.start : part.end]
                    covered.update(range(part.start, part.end))
        assert covered == set(range(len(original.quote)))
    assert any(len(w.spans) > 1 for w in windows)


async def test_embedding_client_splits_batches_and_pools_to_one_vector_per_source():
    import json

    import httpx

    from domain_kg.providers.embeddings import EmbeddingClient, EmbeddingProfile
    from domain_kg.providers.profiles import embedding_space_id

    source = document("evidence")
    chunk = chunk_document(source, ChunkConfig())[0]
    approved, connection = configured(chunk)
    approved = approved.model_copy(
        update={
            "embedding_context_tokens": 256,
            "embedding_batch_size": 1,
            "embedding_query_prefix": "Query: ",
        }
    )
    profile = EmbeddingProfile(
        org_id=chunk.org_id,
        kg_id=chunk.kg_id,
        id=approved.id,
        revision=approved.revision,
        model=approved.embedding_model,
        dimensions=3,
        space_id=embedding_space_id(approved, connection),
    )
    calls, authorizations = [], []

    def respond(request):
        payload = json.loads(request.content)
        assert len(payload["input"]) == 1
        text = payload["input"][0]
        assert text.startswith("Query: ") and len(text.encode()) + 128 <= 256
        calls.append(text)
        return httpx.Response(
            200,
            json={
                "model": profile.model,
                "data": [{"index": 0, "embedding": [1.0, 0.0, 0.0]}],
                "usage": {"prompt_tokens": 10},
            },
        )

    async def authorize():
        authorizations.append(True)

    async def usage(_):
        pass

    client = EmbeddingClient(
        approved,
        connection,
        authorize=authorize,
        record_usage=usage,
        transport=httpx.MockTransport(respond),
    )
    try:
        assert await client.embed(["细胞" * 100, "short"], profile, query=True) == [
            [1.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
        ]
        assert len(calls) > 2 and len(authorizations) == 2 * len(calls)
        changed = approved.model_copy(update={"embedding_context_tokens": 512})
        assert embedding_space_id(changed, connection) != profile.space_id
    finally:
        await client.close()
