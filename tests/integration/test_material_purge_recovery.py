"""Cleanup interruption and invalid shared references preserve source boundaries."""
# ruff: noqa: F811

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.identity.models import Action
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.materials.lifecycle import MaterialLifecycle
from domain_kg.materials.models import BlobRef, RemoveMaterial
from domain_kg.materials.purge_models import PurgeMaterial
from domain_kg.storage.blobs import BlobStore

pytestmark = pytest.mark.storage


async def expired(publishing, team):
    materials, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    lifecycle = MaterialLifecycle(materials, queue)
    removed = await lifecycle.remove(
        editor,
        RemoveMaterial(
            kg_id=job.kg_id,
            material_id=source.material_id,
            expected_revision=1,
            action="delete",
            idempotency_key="expired-for-purge",
        ),
    )
    ns = {"org_id": str(editor.org_id), "kg_id": str(job.kg_id)}
    await queue.database.material_access.update_one(
        ns | {"id": str(source.material_id)},
        {"$set": {"purge_after": datetime.now(UTC) - timedelta(seconds=1)}},
    )
    owner = await queue.policy.resolve_access(team[2]["owner"], [job.kg_id], Action.PURGE)
    command = PurgeMaterial(
        kg_id=job.kg_id,
        material_id=source.material_id,
        expected_revision=removed.revision,
        reason="Expired recovery fixture",
        idempotency_key="purge-recovery",
    )
    return source, lifecycle, owner, command


async def test_invalid_foreign_reference_prevents_physical_object_deletion(publishing, team):
    source, lifecycle, owner, command = await expired(publishing, team)
    materials, _, queue, coordinator, _, _ = publishing
    stores = await materials.router.for_sources(owner, command.kg_id)
    original = await stores.authority._database.version_blobs.find_one(
        {"org_id": str(owner.org_id), "kg_id": str(command.kg_id), "id": str(source.version_id)}
    )
    # A broken pointer in another KG may not authorize deleting the physical key it names.
    await stores.authority._database.version_blobs.insert_one(
        {key: value for key, value in original.items() if key != "_id"}
        | {"kg_id": str(uuid4()), "id": str(uuid4()), "material_id": str(uuid4())}
    )
    await lifecycle.purge(owner, command)
    job = await queue.claim("invalid-reference-purger", kinds=("purge_material",))
    await JobRunner(queue, configured_pipeline(queue, coordinator.providers)).run_job(job)
    status = await queue.get(owner, job.id)
    assert status.stage == "failed" and "PROJECTION_INCONSISTENT" in status.warnings
    blob = BlobRef.model_validate(original["blob"])
    assert b"".join([part async for part in BlobStore(stores.blobs).open(blob)])
    assert not await queue.policy.material_visible(owner, command.kg_id, source.material_id)


async def test_partial_object_cleanup_resumes_without_duplicate_quota_release(
    publishing, team, monkeypatch
):
    source, lifecycle, owner, command = await expired(publishing, team)
    materials, _, queue, coordinator, _, _ = publishing
    stores = await materials.router.for_sources(owner, command.kg_id)
    prefix = f"{owner.org_id}/{command.kg_id}/{source.material_id}/"
    client = stores.blobs.client
    original_delete = client.delete_object
    calls = []

    def interrupted(**kwargs):
        calls.append(kwargs["Key"])
        if len(calls) == 2:
            raise ClientError({"Error": {"Code": "InternalError"}}, "DeleteObject")
        return original_delete(**kwargs)

    monkeypatch.setattr(client, "delete_object", interrupted)
    quota_key = {"org_id": str(owner.org_id), "kg_id": str(command.kg_id)}
    before = await queue.database.kg_quotas.find_one(quota_key)
    pending = await lifecycle.purge(owner, command)
    job = await queue.claim("interrupted-purger", kinds=("purge_material",))
    runner = JobRunner(queue, configured_pipeline(queue, coordinator.providers))
    await runner.run_job(job)
    assert (await queue.get(owner, job.id)).stage == "queued"
    assert (await queue.database.kg_quotas.find_one(quota_key))["committed_bytes"] == before[
        "committed_bytes"
    ]
    await queue.cancel(owner, job.id, idempotency_key="pause-interrupted-cleanup")
    await queue.retry(owner, job.id, idempotency_key="resume-interrupted-cleanup")
    next_job = await queue.claim("resumed-purger", kinds=("purge_material",))
    assert next_job.id == job.id and next_job.attempt_id != job.attempt_id
    await runner.run_job(next_job)
    assert (await lifecycle.purge(owner, command)).state == "purged"
    assert (await queue.get(owner, pending.job_id)).stage == "ready"
    assert (await queue.database.kg_quotas.find_one(quota_key))["committed_bytes"] == 0
    listing = await asyncio.to_thread(
        client.list_objects_v2, Bucket=stores.blobs.bucket, Prefix=prefix
    )
    assert listing.get("KeyCount", 0) == 0
    await lifecycle.purge(owner, command)
    assert (await queue.database.kg_quotas.find_one(quota_key))["committed_bytes"] == 0


async def test_active_read_pin_delays_cleanup_without_creating_a_job(publishing, team):
    from domain_kg.errors import DomainError

    source, lifecycle, owner, command = await expired(publishing, team)
    queue = publishing[2]
    ns = {"org_id": str(owner.org_id), "kg_id": str(command.kg_id)}
    pin = ns | {
        "id": str(uuid4()),
        "material_ids": [str(source.material_id)],
        "expires_at": datetime.now(UTC) + timedelta(minutes=1),
    }
    await queue.database.read_pins.insert_one(pin)
    before = await queue.database.jobs.count_documents(ns)
    with pytest.raises(DomainError):
        await lifecycle.purge(owner, command)
    assert await queue.database.jobs.count_documents(ns) == before
    await queue.database.read_pins.update_one(
        ns | {"id": pin["id"]}, {"$set": {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}}
    )
    assert (await lifecycle.purge(owner, command)).state == "pending"


async def test_source_owned_orphan_is_removed_without_touching_another_material(publishing, team):
    import hashlib

    from domain_kg.materials.models import AddMaterial, BlobScope

    source, lifecycle, owner, command = await expired(publishing, team)
    materials, editor, queue, coordinator, _, _ = publishing
    other = await materials.add(
        editor,
        AddMaterial(
            kg_id=command.kg_id,
            title="Keep this material",
            inline_text="Independent original",
            idempotency_key="purge-orphan-independent",
        ),
    )
    stores = await materials.router.for_sources(owner, command.kg_id)
    content = b"Interrupted unregistered artifact"

    async def stream():
        yield content

    await BlobStore(stores.blobs).put(
        stream(),
        hashlib.sha256(content).hexdigest(),
        BlobScope(
            org_id=owner.org_id,
            kg_id=command.kg_id,
            material_id=source.material_id,
            version_id=source.version_id,
        ),
    )
    await lifecycle.purge(owner, command)
    job = await queue.claim("orphan-purger", kinds=("purge_material",))
    await JobRunner(queue, configured_pipeline(queue, coordinator.providers)).run_job(job)
    assert (await lifecycle.purge(owner, command)).state == "purged"
    listing = await asyncio.to_thread(
        stores.blobs.client.list_objects_v2,
        Bucket=stores.blobs.bucket,
        Prefix=f"{owner.org_id}/{command.kg_id}/{source.material_id}/",
    )
    assert listing.get("KeyCount", 0) == 0
    assert b"".join(
        [
            part
            async for part in materials.original(
                owner, command.kg_id, other.material_id, other.version_id
            )
        ]
    )


async def test_owner_revocation_after_detachment_prevents_physical_cleanup(
    publishing, team, monkeypatch
):
    from domain_kg.materials.purge import PurgeService

    source, lifecycle, owner, command = await expired(publishing, team)
    materials, _, queue, coordinator, _, _ = publishing
    manager = await queue.policy.resolve_access(
        team[2]["owner"], [command.kg_id], Action.MANAGE_GRANTS
    )
    await queue.policy.set_grant(
        manager,
        command.kg_id,
        "member",
        team[3]["editor"].id,
        "owner",
        idempotency_key="appoint-cleanup-owner",
    )
    actor = await queue.policy.resolve_access(team[2]["editor"], [command.kg_id], Action.PURGE)
    await lifecycle.purge(actor, command)
    manager = await queue.policy.resolve_access(
        team[2]["owner"], [command.kg_id], Action.MANAGE_GRANTS
    )
    detach = PurgeService._detach

    async def revoke_after_detachment(self, job):
        await detach(self, job)
        await queue.policy.set_grant(
            manager,
            command.kg_id,
            "member",
            team[3]["editor"].id,
            None,
            idempotency_key="revoke-cleanup-owner",
        )

    monkeypatch.setattr(PurgeService, "_detach", revoke_after_detachment)
    job = await queue.claim("revoked-purger", kinds=("purge_material",))
    await JobRunner(queue, configured_pipeline(queue, coordinator.providers)).run_job(job)
    owner = await queue.policy.resolve_access(team[2]["owner"], [command.kg_id], Action.READ)
    assert (await queue.get(owner, job.id)).stage == "failed"
    stores = await materials.router.for_sources(owner, command.kg_id)
    row = await stores.authority._database.version_blobs.find_one(
        {"org_id": str(owner.org_id), "kg_id": str(command.kg_id), "id": str(source.version_id)}
    )
    assert b"".join(
        [part async for part in BlobStore(stores.blobs).open(BlobRef.model_validate(row["blob"]))]
    )
    assert not await queue.policy.material_visible(owner, command.kg_id, source.material_id)
