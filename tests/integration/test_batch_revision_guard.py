"""Reprocessing must not overwrite metadata changed while its original is copied."""

import pytest
from test_publication import make_publishing, prepare, ready

from domain_kg.materials.lifecycle import MaterialLifecycle
from domain_kg.materials.models import MaterialMetadataChanges, UpdateMaterialMetadata
from domain_kg.operations.batch import BatchCommand, BatchOperations, BatchTarget
from domain_kg.revisions.journal import PublicationJournal

pytestmark = pytest.mark.storage


async def test_batch_reprocess_rejects_metadata_change_during_original_copy(
    material_service, team, monkeypatch
):
    publishing = await make_publishing(material_service, team)
    materials, access, queue, coordinator, _, _ = publishing
    receipt, job, checkpoint = await prepare(publishing)
    await coordinator.publish(access, await ready(publishing, job, checkpoint))
    lifecycle = MaterialLifecycle(materials, queue)
    original_upload = materials.upload

    async def copy_then_change_metadata(*args, **kwargs):
        uploaded = await original_upload(*args, **kwargs)
        await lifecycle.update_metadata(
            access,
            UpdateMaterialMetadata(
                kg_id=job.kg_id,
                material_id=receipt.material_id,
                expected_revision=1,
                changes=MaterialMetadataChanges(title="Concurrent title"),
                idempotency_key="race-bulk-metadata",
            ),
        )
        return uploaded

    monkeypatch.setattr(materials, "upload", copy_then_change_metadata)
    result = await BatchOperations(queue, lifecycle).execute(
        access,
        job.kg_id,
        BatchCommand(
            operation="process",
            idempotency_key="race-bulk-process",
            items=(BatchTarget(id=receipt.material_id, revision=1, version_id=receipt.version_id),),
        ),
    )
    assert result.items[0].outcome == "failed" and result.items[0].code == "VERSION_CONFLICT"
    view = await PublicationJournal(queue).load(access, job.kg_id)
    assert view.sources[0].version_id == receipt.version_id
    assert (
        await queue.database.jobs.count_documents(
            {"org_id": str(access.org_id), "kg_id": str(job.kg_id), "kind": "replace"}
        )
        == 0
    )
