"""Missing projections restore through isolated provider-free durable attempts."""
# ruff: noqa: F811

import pytest
from qdrant_client import models
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError
from domain_kg.identity.models import Action
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.materials.lifecycle import MaterialLifecycle
from domain_kg.materials.models import RemoveMaterial, RestoreMaterial
from domain_kg.revisions.journal import PublicationJournal

pytestmark = pytest.mark.storage


async def test_missing_projection_is_restored_in_new_attempt_without_models(
    publishing, monkeypatch, team
):
    materials, editor, queue, coordinator, _, _ = publishing
    receipt, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    journal = PublicationJournal(queue)
    old = await journal.load(editor, job.kg_id)
    binding = await queue.router.for_vectors(editor, job.kg_id)
    points, _ = await binding.client.scroll(binding.collection, limit=1)
    await binding.client.delete(
        binding.collection, points_selector=models.PointIdsList(points=[points[0].id]), wait=True
    )
    lifecycle = MaterialLifecycle(materials, queue)
    removed = await lifecycle.remove(
        editor,
        RemoveMaterial(
            kg_id=job.kg_id,
            material_id=receipt.material_id,
            expected_revision=1,
            action="delete",
            idempotency_key="remove-before-recovery",
        ),
    )
    command = RestoreMaterial(
        kg_id=job.kg_id,
        material_id=receipt.material_id,
        expected_revision=removed.revision,
        idempotency_key="restore-missing",
    )
    pending = await lifecycle.restore(editor, command)
    assert pending.state == "pending" and pending.job_id is not None
    assert await lifecycle.restore(editor, command) == pending
    assert not await queue.policy.material_visible(editor, job.kg_id, receipt.material_id)
    assert (await queue.get(editor, pending.job_id)).stage == "queued"
    reader = await queue.policy.resolve_access(team[2]["reader"], [job.kg_id], Action.READ)
    with pytest.raises(DomainError):
        await queue.get(reader, pending.job_id)

    async def forbidden(*args, **kwargs):
        pytest.fail("retained restoration must not call extraction or embedding providers")

    monkeypatch.setattr(coordinator.providers, "extract", forbidden)
    monkeypatch.setattr(coordinator.providers, "embeddings", forbidden)
    repair = await queue.claim("restoration-worker", kinds=("restore_material",))
    assert repair.id == pending.job_id
    await JobRunner(queue, configured_pipeline(queue, coordinator.providers)).run_job(repair)
    assert (await queue.get(editor, repair.id)).stage == "ready"
    assert await queue.policy.material_visible(editor, job.kg_id, receipt.material_id)
    restored = await lifecycle.restore(editor, command)
    assert restored.state == "restored" and restored.revision == removed.revision + 1
    new = await journal.load(editor, job.kg_id)
    assert new.revision == old.revision + 1
    assert new.sources == old.sources
    assert new.projections[0].attempt_id != old.projections[0].attempt_id
    graph = await queue.router.for_graph(editor, job.kg_id)
    records, _, _ = await graph.driver.execute_query(
        "MATCH (n:ScopedProjection {org_id:$org,kg_id:$kg,attempt_id:$attempt}) "
        "RETURN count(n) AS total",
        org=str(job.org_id),
        kg=str(job.kg_id),
        attempt=str(old.projections[0].attempt_id),
    )
    assert records[0]["total"] == old.receipts[0].record_count


async def test_missing_original_never_reopens_visibility(publishing):
    import asyncio

    from domain_kg.materials.models import BlobRef
    from domain_kg.storage.blobs import BlobStore

    materials, editor, queue, coordinator, _, _ = publishing
    receipt, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    stores = await queue.router.for_sources(editor, job.kg_id)
    record = await stores.authority._database.version_blobs.find_one(
        {"org_id": str(job.org_id), "kg_id": str(job.kg_id), "id": str(receipt.version_id)}
    )
    blob = BlobRef.model_validate(record["blob"])
    store = BlobStore(stores.blobs)
    await asyncio.to_thread(
        stores.blobs.client.delete_object,
        Bucket=stores.blobs.bucket,
        Key=store._key(blob, blob.object_id),
    )
    lifecycle = MaterialLifecycle(materials, queue)
    removed = await lifecycle.remove(
        editor,
        RemoveMaterial(
            kg_id=job.kg_id,
            material_id=receipt.material_id,
            expected_revision=1,
            action="delete",
            idempotency_key="remove-with-original-loss",
        ),
    )
    pending = await lifecycle.restore(
        editor,
        RestoreMaterial(
            kg_id=job.kg_id,
            material_id=receipt.material_id,
            expected_revision=removed.revision,
            idempotency_key="restore-with-original-loss",
        ),
    )
    assert pending.state == "pending"
    assert not await queue.policy.material_visible(editor, job.kg_id, receipt.material_id)
    restore = await queue.claim("missing-original-worker", kinds=("restore_material",))
    await JobRunner(queue, configured_pipeline(queue, coordinator.providers)).run_job(restore)
    failed = await queue.get(editor, restore.id)
    assert failed.stage == "queued" and "BACKEND_UNAVAILABLE" in failed.warnings
    assert not await queue.policy.material_visible(editor, job.kg_id, receipt.material_id)


@pytest.mark.parametrize("interruption", ["grant", "delete"])
async def test_staged_restoration_cannot_outlive_current_source_authority(
    publishing, team, interruption
):
    materials, editor, queue, coordinator, _, _ = publishing
    receipt, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    vectors = await queue.router.for_vectors(editor, job.kg_id)
    points, _ = await vectors.client.scroll(vectors.collection, limit=1)
    await vectors.client.delete(
        vectors.collection, points_selector=models.PointIdsList(points=[points[0].id]), wait=True
    )
    lifecycle = MaterialLifecycle(materials, queue)
    removed = await lifecycle.remove(
        editor,
        RemoveMaterial(
            kg_id=job.kg_id,
            material_id=receipt.material_id,
            expected_revision=1,
            action="exclude",
            idempotency_key="exclude-before-stale-restore",
        ),
    )
    pending = await lifecycle.restore(
        editor,
        RestoreMaterial(
            kg_id=job.kg_id,
            material_id=receipt.material_id,
            expected_revision=removed.revision,
            idempotency_key="restore-before-interruption",
        ),
    )
    assert pending.state == "pending"
    restore = await queue.claim("stale-restorer", kinds=("restore_material",))
    pipeline = configured_pipeline(queue, coordinator.providers)
    for step in pipeline.restore_steps[:-1]:
        checkpoint = await step.execute(restore)
        await queue.checkpoint(restore, checkpoint)
    owner = await queue.policy.resolve_access(team[2]["owner"], [job.kg_id], Action.MANAGE_GRANTS)
    if interruption == "grant":
        await queue.policy.set_grant(
            owner,
            job.kg_id,
            "member",
            team[3]["editor"].id,
            None,
            idempotency_key="revoke-staged-restorer",
        )
        owner = await queue.policy.resolve_access(team[2]["owner"], [job.kg_id], Action.READ)
    else:
        await lifecycle.remove(
            editor,
            RemoveMaterial(
                kg_id=job.kg_id,
                material_id=receipt.material_id,
                expected_revision=removed.revision,
                action="delete",
                idempotency_key="delete-staged-restorer",
            ),
        )
    with pytest.raises(DomainError):
        await pipeline.restore_steps[-1].execute(restore)
    assert not await queue.policy.material_visible(owner, job.kg_id, receipt.material_id)
    assert (await PublicationJournal(queue).load_metadata(owner, job.kg_id)).revision == 1


async def test_original_loss_after_embedding_checkpoint_still_blocks_publication(publishing):
    import asyncio

    from domain_kg.materials.models import BlobRef
    from domain_kg.storage.blobs import BlobStore

    materials, editor, queue, coordinator, _, _ = publishing
    receipt, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    vectors = await queue.router.for_vectors(editor, job.kg_id)
    points, _ = await vectors.client.scroll(vectors.collection, limit=1)
    await vectors.client.delete(
        vectors.collection, points_selector=models.PointIdsList(points=[points[0].id]), wait=True
    )
    lifecycle = MaterialLifecycle(materials, queue)
    removed = await lifecycle.remove(
        editor,
        RemoveMaterial(
            kg_id=job.kg_id,
            material_id=receipt.material_id,
            expected_revision=1,
            action="delete",
            idempotency_key="remove-before-checkpoint-loss",
        ),
    )
    await lifecycle.restore(
        editor,
        RestoreMaterial(
            kg_id=job.kg_id,
            material_id=receipt.material_id,
            expected_revision=removed.revision,
            idempotency_key="restore-before-checkpoint-loss",
        ),
    )
    restore = await queue.claim("checkpoint-restorer", kinds=("restore_material",))
    pipeline = configured_pipeline(queue, coordinator.providers)
    for step in pipeline.restore_steps[:-1]:
        checkpoint = await step.execute(restore)
        await queue.checkpoint(restore, checkpoint)
    stores = await queue.router.for_sources(editor, job.kg_id)
    record = await stores.authority._database.version_blobs.find_one(
        {"org_id": str(job.org_id), "kg_id": str(job.kg_id), "id": str(receipt.version_id)}
    )
    blob = BlobRef.model_validate(record["blob"])
    store = BlobStore(stores.blobs)
    await asyncio.to_thread(
        stores.blobs.client.delete_object,
        Bucket=stores.blobs.bucket,
        Key=store._key(blob, blob.object_id),
    )
    with pytest.raises(DomainError):
        await pipeline.restore_steps[-1].execute(restore)
    assert not await queue.policy.material_visible(editor, job.kg_id, receipt.material_id)
    assert (await PublicationJournal(queue).load_metadata(editor, job.kg_id)).revision == 1
