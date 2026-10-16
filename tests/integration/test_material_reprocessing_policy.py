"""Full reprocessing never turns retained-original access into serving visibility."""
# ruff: noqa: F811

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from test_entity_resolution import TEXT
from test_publication import publishing  # noqa: F401

from domain_kg.errors import DomainError
from domain_kg.identity.models import Action
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.materials.lifecycle import MaterialLifecycle
from domain_kg.materials.models import AddMaterial, BlobRef, RemoveMaterial, RestoreMaterial
from domain_kg.revisions.journal import PublicationJournal
from domain_kg.storage.blobs import BlobStore

pytestmark = pytest.mark.storage


async def pending_restore(publishing, *, restricted=False, exclusion=False):
    materials, editor, queue, coordinator, _, _ = publishing
    kg = editor.kg_ids[0]
    source = await materials.add(
        editor,
        AddMaterial(
            kg_id=kg,
            title="Unpublished policy fixture",
            inline_text=TEXT,
            acl_mode="restricted" if restricted else "kg",
            idempotency_key="unpublished-policy-source",
        ),
    )
    lifecycle = MaterialLifecycle(materials, queue)
    removed = await lifecycle.remove(
        editor,
        RemoveMaterial(
            kg_id=kg,
            material_id=source.material_id,
            expected_revision=0,
            action="exclude" if exclusion else "delete",
            idempotency_key="unpublished-policy-remove",
        ),
    )
    pending = await lifecycle.restore(
        editor,
        RestoreMaterial(
            kg_id=kg,
            material_id=source.material_id,
            expected_revision=removed.revision,
            idempotency_key="unpublished-policy-restore",
        ),
    )
    job = await queue.claim("unpublished-policy-worker", kinds=("reprocess_material",))
    assert job and job.id == pending.job_id
    return source, removed, lifecycle, job, configured_pipeline(queue, coordinator.providers)


async def test_reprocessing_requires_current_provider_clearance(publishing, monkeypatch):
    _, editor, queue, coordinator, _, faults = publishing
    source, _, _, job, pipeline = await pending_restore(publishing, restricted=True)

    async def forbidden(*args, **kwargs):
        pytest.fail("unapproved restricted data must not reach extraction or embedding")

    monkeypatch.setattr(coordinator.providers, "extract", forbidden)
    monkeypatch.setattr(coordinator.providers, "embeddings", forbidden)
    await JobRunner(queue, pipeline).run_job(job)
    status = await queue.get(editor, job.id)
    assert status.stage == "failed" and status.warnings
    assert not faults.get("embedding_inputs")
    assert not await queue.policy.material_visible(editor, job.kg_id, source.material_id)
    assert (await PublicationJournal(queue).load_metadata(editor, job.kg_id)).revision == 0


@pytest.mark.parametrize("interruption", ["original", "grant", "delete", "retention"])
async def test_reprocessing_keeps_fence_after_readiness_interruption(
    publishing, team, interruption
):
    materials, editor, queue, coordinator, _, _ = publishing
    source, removed, lifecycle, job, pipeline = await pending_restore(
        publishing, exclusion=interruption == "delete"
    )
    # Keep this bounded explicit worker lease alive while staging the tiny fixture.
    heartbeat = asyncio.create_task(JobRunner(queue, pipeline)._heartbeat(job))
    try:
        for step in pipeline.steps[:-1]:
            checkpoint = await step.execute(job)
            await queue.checkpoint(job, checkpoint)
        assert not await queue.policy.material_visible(editor, job.kg_id, source.material_id)
        assert (await PublicationJournal(queue).load_metadata(editor, job.kg_id)).revision == 0
        if interruption == "original":
            stores = await materials.router.for_sources(editor, job.kg_id)
            row = await stores.authority._database.version_blobs.find_one(
                {"org_id": str(job.org_id), "kg_id": str(job.kg_id), "id": str(source.version_id)}
            )
            blob = BlobRef.model_validate(row["blob"])
            await asyncio.to_thread(
                stores.blobs.client.delete_object,
                Bucket=stores.blobs.bucket,
                Key=BlobStore(stores.blobs)._key(blob, blob.object_id),
            )
        elif interruption == "grant":
            owner = await queue.policy.resolve_access(
                team[2]["owner"], [job.kg_id], Action.MANAGE_GRANTS
            )
            await queue.policy.set_grant(
                owner,
                job.kg_id,
                "member",
                team[3]["editor"].id,
                None,
                idempotency_key="revoke-unpublished-restorer",
            )
        elif interruption == "delete":
            await lifecycle.remove(
                editor,
                RemoveMaterial(
                    kg_id=job.kg_id,
                    material_id=source.material_id,
                    expected_revision=removed.revision,
                    action="delete",
                    idempotency_key="delete-unpublished-restorer",
                ),
            )
        else:
            await queue.database.material_access.update_one(
                {"org_id": str(job.org_id), "kg_id": str(job.kg_id), "id": str(source.material_id)},
                {"$set": {"purge_after": datetime.now(UTC) - timedelta(seconds=1)}},
            )
        with pytest.raises(DomainError):
            await pipeline.steps[-1].execute(job)
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
    owner = await queue.policy.resolve_access(team[2]["owner"], [job.kg_id], Action.READ)
    assert not await queue.policy.material_visible(owner, job.kg_id, source.material_id)
    assert (await PublicationJournal(queue).load_metadata(owner, job.kg_id)).revision == 0
