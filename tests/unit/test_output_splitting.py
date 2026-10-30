"""Only explicit output truncation permits a bounded retry on smaller source windows."""

from uuid import uuid4

import pytest
from test_extraction_validation import proposal, source

from domain_kg.errors import DomainError
from domain_kg.ingestion.extraction import validate_extraction
from domain_kg.providers.extraction import ExtractionOutputLimit


@pytest.mark.parametrize("multiple_blocks", [False, True])
async def test_output_split_preserves_boundary_context_with_exact_offsets(multiple_blocks):
    from test_chunking import document_blocks

    from domain_kg.ingestion.chunking import chunk_document, tokens
    from domain_kg.ingestion.models import ChunkConfig
    from domain_kg.ingestion.output_splitting import extract_with_splitting

    text = "Alpha uses Beta. " * 30
    parent = (
        chunk_document(document_blocks([text, text]), ChunkConfig())[0]
        if multiple_blocks
        else source(text * 2)
    )
    template, schema = proposal(parent)
    seen = []

    async def produce(chunk):
        if chunk.id == parent.id:
            raise ExtractionOutputLimit()
        seen.append(chunk)
        return template.model_copy(update={"chunk_id": chunk.id, "entities": (), "assertions": ()})

    await extract_with_splitting(parent, schema, template.provider_profile_id, produce)
    assert len(seen) == 2
    left, right = seen
    assert left.spans[-1].location == right.spans[0].location
    assert left.spans[-1].start < right.spans[0].start < left.spans[-1].end
    overlap = left.spans[-1].quote[right.spans[0].start - left.spans[-1].start :]
    assert 0 < tokens(overlap) <= 100
    for child in seen:
        assert len(child.text.encode()) < len(parent.text.encode())
        for span in child.spans:
            original = next(s for s in parent.spans if s.location == span.location)
            assert (
                span.quote
                == original.quote[span.start - original.start : span.end - original.start]
            )
    for original in parent.spans:
        covered = {
            i
            for c in seen
            for s in c.spans
            if s.location == original.location
            for i in range(s.start, s.end)
        }
        assert covered == set(range(original.start, original.end))

async def test_split_output_keeps_full_source_coverage_and_distinct_local_endpoints():
    from domain_kg.ingestion.output_splitting import extract_with_splitting

    parent = source("Alpha uses Beta.\nAlpha uses Beta.")
    _, schema = proposal(parent)
    profile = uuid4()
    seen = []

    async def produce(chunk):
        seen.append(chunk)
        if chunk.id == parent.id:
            raise ExtractionOutputLimit()
        batch, _ = proposal(chunk)
        return batch.model_copy(
            update={
                "provider_profile_id": profile,
                "entities": tuple(
                    e.model_copy(update={"description": "", "aliases": (), "identifier": None})
                    for e in batch.entities
                ),
            }
        )

    batch = await extract_with_splitting(parent, schema, profile, produce)
    assert len(seen) == 3 and len(batch.entities) == 4 and len(batch.assertions) == 2
    assert len({e.local_id for e in batch.entities}) == 4
    assert validate_extraction(parent, schema, batch) == batch
    covered = {i for chunk in seen[1:] for span in chunk.spans for i in range(span.start, span.end)}
    assert covered == set(range(len(parent.text)))
    assert batch.chunk_id == parent.id and batch.warnings


async def test_rejection_is_never_retried_and_truncation_has_a_hard_depth_limit():
    from domain_kg.ingestion.output_splitting import extract_with_splitting

    parent = source("Alpha uses Beta.\nAlpha uses Beta.")
    _, schema = proposal(parent)
    calls = 0

    async def rejected(_):
        nonlocal calls
        calls += 1
        raise DomainError("EVIDENCE_VALIDATION_FAILED", "Invalid source")

    with pytest.raises(DomainError):
        await extract_with_splitting(parent, schema, uuid4(), rejected)
    assert calls == 1

    async def truncated(_):
        nonlocal calls
        calls += 1
        raise ExtractionOutputLimit()

    calls = 0
    with pytest.raises(ExtractionOutputLimit):
        await extract_with_splitting(parent, schema, uuid4(), truncated, max_depth=0)
    assert calls == 1


async def test_output_recovery_balances_text_in_uneven_pdf_lines():
    from test_chunking import document_blocks

    from domain_kg.ingestion.chunking import chunk_document, tokens
    from domain_kg.ingestion.models import ChunkConfig
    from domain_kg.ingestion.output_splitting import extract_with_splitting

    document = document_blocks(["Label."] * 19 + ["Dense source evidence. " * 240])
    parent = chunk_document(document, ChunkConfig(target_tokens=4096))[0]
    template, schema = proposal(parent)
    ceiling = tokens(parent.text) // 2
    accepted = []

    async def produce(chunk):
        if tokens(chunk.text) > ceiling:
            raise ExtractionOutputLimit()
        accepted.append(chunk)
        return template.model_copy(update={"chunk_id": chunk.id, "entities": (), "assertions": ()})

    await extract_with_splitting(parent, schema, template.provider_profile_id, produce)
    assert accepted
    for original in parent.spans:
        covered = set()
        for chunk in accepted:
            for span in chunk.spans:
                if span.location == original.location:
                    assert span.quote == original.quote[span.start : span.end]
                    covered.update(range(span.start, span.end))
        assert covered == set(range(original.start, original.end))
