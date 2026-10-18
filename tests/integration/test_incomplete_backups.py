"""Incomplete backups are collectible only after durable writes have settled."""
# ruff: noqa: F811

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.operations.backup import BackupService
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import BackupCommand, RetentionCommand
from domain_kg.operations.package_erasure import PackageErasure
from domain_kg.operations.retention import RetentionService
from domain_kg.operations.storage import PackageStore

pytestmark = pytest.mark.storage


async def failed_capture(publishing, team, monkeypatch, *, uncertain=False):
    _, editor, queue, coordinator, _, _ = publishing
    source, native, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, native, checkpoint))
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.CONFIGURE)
    await OwnerOperations(queue).submit(
        owner,
        BackupCommand(
            kg_id=source.kg_id,
            expected_published_revision=1,
            idempotency_key="incomplete-capture",
        ),
    )
    job = await queue.claim("incomplete-capture", kinds=("backup",))
    service = BackupService(queue)
    write = service._store
    if uncertain:
        put = PackageStore.put

        async def lost_acknowledgement(store, reference, chunks):
            await put(store, reference, chunks)
            raise DomainError(
                ErrorCode.BACKEND_UNAVAILABLE, "Synthetic lost remote acknowledgement"
            )

        monkeypatch.setattr(PackageStore, "put", lost_acknowledgement)

    async def interrupted(context, reference, chunks):
        await write(context, reference, chunks)
        raise DomainError(ErrorCode.BACKEND_UNAVAILABLE, "Synthetic post-write interruption")

    monkeypatch.setattr(service, "_store", interrupted)
    with pytest.raises(DomainError):
        await service.run(job)
    await queue.fail(job, DomainError(ErrorCode.BACKEND_UNAVAILABLE, "Synthetic capture failed"))
    ns = {"org_id": str(job.org_id), "kg_id": str(job.kg_id)}
    raw = await queue.database.backup_objects.find_one(ns | {"job_id": str(job.id)})
    from domain_kg.operations.packages import PackageObject

    if raw is None:
        tracked = await queue.database.backup_writes.find_one(ns | {"job_id": str(job.id)})
        reference = PackageObject.model_validate(tracked["reference"])
        assert tracked["state"] == "uncertain"
    else:
        reference = PackageObject.model_validate({k: raw[k] for k in PackageObject.model_fields})
    store = PackageStore((await queue.router.for_sources(owner, job.kg_id)).blobs)
    assert b"".join([chunk async for chunk in store.open(reference)])
    monkeypatch.setattr(
        "domain_kg.operations.retention_targets.now",
        lambda: datetime.now(UTC) + timedelta(days=2),
    )
    return owner, queue, job, store, reference


async def test_settled_failed_capture_is_cleaned_without_touching_originals(
    publishing, team, monkeypatch
):
    owner, queue, original, store, reference = await failed_capture(publishing, team, monkeypatch)
    operations = OwnerOperations(queue)
    command = RetentionCommand(
        kg_id=original.kg_id,
        expected_published_revision=1,
        resources="incomplete_backups",
        idempotency_key="erase-incomplete-capture",
    )
    receipt = await operations.submit(owner, command)
    assert await operations.submit(owner, command) == receipt
    with pytest.raises(DomainError) as claimed:
        await queue.retry(owner, original.id, idempotency_key="retry-during-capture-cleanup")
    assert claimed.value.code == ErrorCode.VERSION_CONFLICT
    job = await queue.claim("incomplete-cleaner", kinds=("retention",))
    await RetentionService(queue).run(job)
    assert await queue.accepted(job)
    await RetentionService(queue).run(job)
    with pytest.raises(DomainError):
        async for _ in store.open(reference):
            pass
    current = await queue.database.kgs.find_one({"org_id": str(job.org_id), "id": str(job.kg_id)})
    assert current["published_revision"] == 1
    assert (
        await queue.database.material_access.count_documents(
            {
                "org_id": str(job.org_id),
                "kg_id": str(job.kg_id),
                "tombstoned": False,
            }
        )
        == 1
    )


@pytest.mark.parametrize("state", ["pending", "uncertain", "untracked", "remote_uncertain"])
async def test_pending_write_or_untracked_capture_is_not_collected(
    publishing, team, monkeypatch, state
):
    owner, queue, original, store, reference = await failed_capture(
        publishing, team, monkeypatch, uncertain=state == "remote_uncertain"
    )
    ns = {"org_id": str(original.org_id), "kg_id": str(original.kg_id)}
    if state == "untracked":
        await queue.database.backup_attempts.delete_many(ns | {"job_id": str(original.id)})
    elif state != "remote_uncertain":
        await queue.database.backup_writes.update_many(
            ns | {"job_id": str(original.id)}, {"$set": {"state": state}}
        )
    await OwnerOperations(queue).submit(
        owner,
        RetentionCommand(
            kg_id=original.kg_id,
            expected_published_revision=1,
            resources="incomplete_backups",
            idempotency_key="refuse-pending-capture",
        ),
    )
    job = await queue.claim("pending-cleaner", kinds=("retention",))
    await RetentionService(queue).run(job)
    assert await queue.accepted(job)
    assert b"".join([chunk async for chunk in store.open(reference)])


async def test_failed_old_attempt_is_collectible_after_a_successful_retry(
    publishing, team, monkeypatch
):
    owner, queue, original, store, reference = await failed_capture(publishing, team, monkeypatch)
    await queue.retry(owner, original.id, idempotency_key="retry-before-capture-collection")
    successor = await queue.claim("successful-capture-retry", kinds=("backup",))
    assert successor.attempt_id != original.attempt_id
    service = BackupService(queue)
    await service.run(successor)
    accepted = await service.verify(owner, original.kg_id, original.target_id)
    await OwnerOperations(queue).submit(
        owner,
        RetentionCommand(
            kg_id=original.kg_id,
            expected_published_revision=1,
            resources="incomplete_backups",
            idempotency_key="collect-earlier-failed-capture",
        ),
    )
    cleanup = await queue.claim("earlier-capture-cleanup", kinds=("retention",))
    await RetentionService(queue).run(cleanup)
    assert await queue.accepted(cleanup)
    with pytest.raises(DomainError):
        async for _ in store.open(reference):
            pass
    assert await service.verify(owner, original.kg_id, original.target_id) == accepted


async def test_interrupted_collection_resumes_without_accepting_partial_erasure(
    publishing, team, monkeypatch
):
    owner, queue, original, store, reference = await failed_capture(publishing, team, monkeypatch)
    await OwnerOperations(queue).submit(
        owner,
        RetentionCommand(
            kg_id=original.kg_id,
            expected_published_revision=1,
            resources="incomplete_backups",
            idempotency_key="interrupted-capture-collection",
        ),
    )
    job = await queue.claim("interrupted-capture-cleaner", kinds=("retention",))
    erase = PackageErasure.erase

    async def interrupted(erasure, reference):
        await erase(erasure, reference)
        raise DomainError(ErrorCode.BACKEND_UNAVAILABLE, "Synthetic lost cleanup response")

    monkeypatch.setattr(PackageErasure, "erase", interrupted)
    service = RetentionService(queue)
    with pytest.raises(DomainError):
        await service.run(job)
    assert not await queue.accepted(job)
    await queue.fail(job, DomainError(ErrorCode.BACKEND_UNAVAILABLE, "Synthetic cleanup failure"))
    await queue.retry(owner, job.id, idempotency_key="retry-incomplete-collection")
    retry = await queue.claim("resumed-capture-cleaner", kinds=("retention",))
    monkeypatch.setattr(PackageErasure, "erase", erase)
    await service.run(retry)
    assert await queue.accepted(retry)
    with pytest.raises(DomainError):
        await service.run(job)
    with pytest.raises(DomainError):
        async for _ in store.open(reference):
            pass


@pytest.mark.parametrize("field", ["kg_id", "backup_id", "attempt_id"])
async def test_forged_write_reference_cannot_grant_erasure(publishing, team, monkeypatch, field):
    owner, queue, original, store, reference = await failed_capture(publishing, team, monkeypatch)
    await queue.database.backup_writes.update_many(
        {"org_id": str(original.org_id), "kg_id": str(original.kg_id), "job_id": str(original.id)},
        {"$set": {"reference." + field: str(uuid4())}},
    )
    with pytest.raises(DomainError):
        await OwnerOperations(queue).submit(
            owner,
            RetentionCommand(
                kg_id=original.kg_id,
                expected_published_revision=1,
                resources="incomplete_backups",
                idempotency_key="reject-forged-capture-reference",
            ),
        )
    assert b"".join([chunk async for chunk in store.open(reference)])
