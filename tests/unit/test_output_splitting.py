"""Only explicit output truncation permits a bounded retry on smaller source windows."""

from uuid import uuid4

import pytest
from test_extraction_validation import proposal, source

from domain_kg.errors import DomainError
from domain_kg.ingestion.extraction import validate_extraction
from domain_kg.providers.extraction import ExtractionOutputLimit


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
