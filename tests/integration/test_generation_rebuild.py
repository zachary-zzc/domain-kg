"""Tiny current-space rebuilds keep the accepted view through failure and recovery."""
# ruff: noqa: F811

import pytest
from test_maintenance_admission import accepted_human_edit
from test_publication import publishing  # noqa: F401

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import GenerationCommand, MaintenanceCommand
from domain_kg.revisions.journal import PublicationJournal
from domain_kg.revisions.publication import verify_accepted
from domain_kg.storage.qdrant import VectorProjection

pytestmark = pytest.mark.storage


async def test_rebuild_failure_retry_repeat_and_repair_preserve_human_history(
    publishing,
    team,
    monkeypatch,
):
    from domain_kg.operations.generation import GenerationService
    from domain_kg.operations.maintenance import MaintenanceService

    source, alpha, curation = await accepted_human_edit(publishing)
    _, editor, queue, _, _, faults = publishing
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.CONFIGURE)
    ns = {"org_id": str(source.org_id), "kg_id": str(source.kg_id)}
    history = await queue.database.curation_revisions.find_one(
        ns | {"id": str(curation.revision_id)}
    )
    embedding_calls = len(faults.get("embedding_inputs", []))
    kg = await queue.database.kgs.find_one({"org_id": ns["org_id"], "id": ns["kg_id"]})
    original_generation, original_manifest = kg["generation"], kg["published_manifest_id"]
    service = GenerationService(queue)
    operations = OwnerOperations(queue)
    for revision in (2, 3):
        receipt = await operations.submit(
            owner,
            GenerationCommand(
                kg_id=source.kg_id,
                expected_published_revision=revision,
                idempotency_key=f"rebuild-{revision}",
            ),
        )
        job = await queue.claim("actual-generation-owner", kinds=("generation",))
        assert job.id == receipt.job_id
        if revision == 2:

            async def fail_vector(adapter, rows, vectors):
                raise DomainError(
                    ErrorCode.BACKEND_UNAVAILABLE, "Synthetic generation interruption."
                )

            monkeypatch.setattr(VectorProjection, "write", fail_vector)
            with pytest.raises(DomainError):
                await service.run(job)
            still = await queue.database.kgs.find_one({"org_id": ns["org_id"], "id": ns["kg_id"]})
            assert (
                still["generation"],
                still["published_manifest_id"],
                still["published_revision"],
            ) == (original_generation, original_manifest, 2)
            assert (
                await GraphService(queue).get_node(editor, source.kg_id, alpha)
            ).name == "Reviewed Alpha"
            assert await queue.database.generation_results.count_documents(ns) == 0
            monkeypatch.undo()
            await queue.fail(
                job,
                DomainError(ErrorCode.BACKEND_UNAVAILABLE, "Synthetic generation interruption."),
            )
            await queue.retry(owner, job.id, idempotency_key="retry-generation")
            retry = await queue.claim("generation-retry", kinds=("generation",))
            assert retry.id == job.id and retry.attempt_id != job.attempt_id
            job = retry
        await service.run(job)
        assert await queue.accepted(job)
        await service.run(job)
        await queue.finish(job)
        current = await queue.database.kgs.find_one({"org_id": ns["org_id"], "id": ns["kg_id"]})
        assert current["published_revision"] == revision + 1
        assert current["generation"] != original_generation
        view = await PublicationJournal(queue).load_metadata(owner, source.kg_id)
        assert len(view.projections) == 2
        for binding in view.projections:
            await verify_accepted(queue, owner, binding)
        assert (
            await GraphService(queue).get_node(editor, source.kg_id, alpha)
        ).name == "Reviewed Alpha"
        assert (
            await queue.database.curation_revisions.find_one(ns | {"id": str(curation.revision_id)})
            == history
        )
        assert not await queue.database.read_pins.find_one(
            ns | {"job_id": str(job.id), "kind": "generation_capture"}
        )
    await operations.submit(
        owner,
        MaintenanceCommand(
            kg_id=source.kg_id,
            expected_published_revision=4,
            curation_id=curation.revision_id,
            idempotency_key="repair-after-generation",
        ),
    )
    repair = await queue.claim("actual-repair", kinds=("maintenance",))
    await MaintenanceService(queue).run(repair)
    assert await queue.accepted(repair)
    assert (
        await GraphService(queue).get_node(editor, source.kg_id, alpha)
    ).name == "Reviewed Alpha"
    assert len(faults.get("embedding_inputs", [])) == embedding_calls
