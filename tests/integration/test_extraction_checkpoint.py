"""An interrupted extraction reuses only validated, source-bound chunk results."""

import pytest
from test_publication import make_publishing

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.materials.models import AddMaterial

pytestmark = pytest.mark.storage


@pytest.mark.parametrize(
    "semantics,window,output",
    [
        ("legacy", 4096, "json_schema"),
        ("source_roles_v1", 8192, "json_schema"),
        ("source_roles_v1", 16384, "json_object"),
    ],
)
async def test_resume_keeps_completed_chunks_but_never_publishes_incomplete_document(
    material_service, team, monkeypatch, semantics, window, output
):
    from test_retrieval_corpus import corpus_extraction

    from domain_kg.ingestion.models import ChunkConfig

    publishing = await make_publishing(
        material_service,
        team,
        extraction_factory=corpus_extraction,
        # Force multiple windows: the test exercises interruption, not adaptive sizing.
        chunk_config=ChunkConfig(target_tokens=200, overlap_tokens=50),
        profile_overrides={
            "extraction_context_tokens": window,
            "schema_in_context": False,
            "max_completion_tokens": 512,
            "extraction_semantics": semantics,
            "extraction_response_format": output,
            "extraction_enable_thinking": False if output == "json_object" else None,
        },
    )
    materials, access, queue, coordinator, steps, _ = publishing
    source = await materials.add(
        access,
        AddMaterial(
            kg_id=access.kg_ids[0],
            inline_text="Independent source evidence. " * 80,
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


async def test_evidence_correction_is_charged_once_per_request_and_cached_before_publication(
    material_service, team
):
    from test_entity_resolution import batch_for
    from test_publication import prepare, ready

    calls = []

    def candidate(chunk, profile_id):
        calls.append(chunk.id)
        batch = batch_for(chunk, profile_id)
        if len(calls) == 1:
            return batch.model_copy(
                update={
                    "entities": (
                        batch.entities[0].model_copy(update={"name": "Invented"}),
                        batch.entities[1],
                    )
                }
            )
        return batch

    publishing = await make_publishing(material_service, team, extraction_factory=candidate)
    _, access, queue, coordinator, steps, _ = publishing
    _, job, checkpoint = await prepare(publishing, key="correct-evidence")
    assert len(calls) == 2
    scope = {"org_id": str(job.org_id), "kg_id": str(job.kg_id)}
    usage = await queue.database.provider_usage.find_one(scope)
    assert usage["calls"] == 2 and usage["input_tokens"] == 200
    counts = await queue.database.provider_budget_days.find_one(scope | {"scope": "kg"})
    assert counts["counts"]["spent_calls"] == 2
    assert counts["counts"]["reserved_calls"] == 0
    assert (await queue.database.kgs.find_one({"id": str(job.kg_id)}))["published_revision"] == 0
    extraction = next(step for step in steps if step.stage == "extracting")
    await extraction.execute(job)
    assert len(calls) == 2
    await coordinator.publish(access, await ready(publishing, job, checkpoint))
    assert (await queue.database.kgs.find_one({"id": str(job.kg_id)}))["published_revision"] == 1


async def test_bad_claims_do_not_block_publication_of_independently_valid_facts(
    material_service, team
):
    from test_entity_resolution import batch_for
    from test_publication import prepare, ready

    from domain_kg.revisions.journal import PublicationJournal
    from domain_kg.revisions.publication import accepted_document

    def candidate(chunk, profile):
        batch = batch_for(chunk, profile)
        invalid = batch.entities[0].model_copy(update={"local_id": "bad", "name": "Invented"})
        return batch.model_copy(
            update={
                "entities": (*batch.entities, invalid),
                "assertions": (
                    *batch.assertions,
                    batch.assertions[0].model_copy(update={"subject": "bad"}),
                ),
            }
        )

    publishing = await make_publishing(material_service, team, extraction_factory=candidate)
    _, access, queue, coordinator, _, _ = publishing
    receipt, job, checkpoint = await prepare(publishing, key="partial-evidence")
    await coordinator.publish(access, await ready(publishing, job, checkpoint))
    result = await queue.get(access, receipt.job_id)
    assert result.stage == "ready_with_warnings"
    assert set(result.warnings) == {
        "extraction_rejected_entities:1",
        "extraction_rejected_assertions:1",
    }
    view = await PublicationJournal(queue).load(access, job.kg_id)
    document, _, _ = await accepted_document(queue, access, view.projections[0])
    assert len([row for row in document.rows if row.kind == "observation"]) == 2
    assert len([row for row in document.rows if row.kind == "support"]) == 1
    assert all(
        row.observation is None or row.observation.name != "Invented" for row in document.rows
    )
