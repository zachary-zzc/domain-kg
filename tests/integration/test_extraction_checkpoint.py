"""An interrupted extraction reuses only validated, source-bound chunk results."""

import pytest
from test_entity_resolution import TEXT
from test_publication import make_publishing

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.materials.models import AddMaterial

pytestmark = pytest.mark.storage


async def test_resume_keeps_completed_chunks_but_never_publishes_incomplete_document(
    material_service, team, monkeypatch
):
    publishing = await make_publishing(
        material_service,
        team,
        profile_overrides={
            "extraction_context_tokens": 4096,
            "schema_in_context": False,
            "max_completion_tokens": 512,
        },
    )
    materials, access, queue, coordinator, steps, _ = publishing
    source = await materials.add(
        access,
        AddMaterial(
            kg_id=access.kg_ids[0],
            inline_text=TEXT * 40,
            title="Interrupted extraction",
            idempotency_key="resume-extract",
        ),
    )
    job = await queue.claim("resume-extraction")
    calls = {}
    original = coordinator.providers.extract
    failing = True

    async def extract(actor, chunk, schema, **kwargs):
        calls[chunk.id] = calls.get(chunk.id, 0) + 1
        if failing and len(calls) == 2:
            raise DomainError(ErrorCode.PROVIDER_UNAVAILABLE, "Interrupted provider")
        return await original(actor, chunk, schema, **kwargs)

    monkeypatch.setattr(coordinator.providers, "extract", extract)
    for step in steps:
        if step.stage == "extracting":
            break
        checkpoint = await step.execute(job)
        await queue.checkpoint(job, checkpoint)
    with pytest.raises(DomainError):
        await step.execute(job)
    first = next(iter(calls))
    assert (await queue.database.kgs.find_one({"id": str(job.kg_id)}))["published_revision"] == 0
    failing = False
    checkpoint = await step.execute(job)
    await queue.checkpoint(job, checkpoint)
    assert calls[first] == 1  # Reissuing a completed real model call would waste the budget.
    assert checkpoint.stage == "extracting" and source.version_id == job.target_id
