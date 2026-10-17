"""Only a live owner backup may copy fenced accepted evidence; normal reads stay fenced."""
# ruff: noqa: F811

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.jobs.models import Checkpoint
from domain_kg.materials.lifecycle import MaterialLifecycle
from domain_kg.materials.models import RemoveMaterial
from domain_kg.materials.purge_models import PurgeMaterial
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import BackupCommand
from domain_kg.revisions.journal import PublicationJournal, read_artifact

pytestmark = pytest.mark.storage


async def test_live_backup_permit_reads_only_its_pinned_source_and_blocks_purge(publishing, team):
    from domain_kg.operations.capture import BackupCapture

    materials, editor, queue, coordinator, _, _ = publishing
    source, original_job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, original_job, checkpoint))
    lifecycle = MaterialLifecycle(materials, queue)
    removed = await lifecycle.remove(
        editor,
        RemoveMaterial(
            kg_id=source.kg_id,
            material_id=source.material_id,
            action="delete",
            expected_revision=1,
            idempotency_key="backup-fenced-source",
        ),
    )
    ns = {"org_id": str(editor.org_id), "kg_id": str(source.kg_id)}
    await queue.database.material_access.update_one(
        ns | {"id": str(source.material_id)},
        {"$set": {"purge_after": datetime.now(UTC) - timedelta(seconds=1)}},
    )
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.BACKUP)
    operations = OwnerOperations(queue)
    receipt = await operations.submit(
        owner,
        BackupCommand(
            kg_id=source.kg_id,
            expected_published_revision=1,
            idempotency_key="capture-fenced-source",
        ),
    )
    job = await queue.claim("backup-capture", kinds=("backup",))
    assert job.id == receipt.job_id
    capture = BackupCapture(queue)
    view = await capture.start(job)
    binding = view.projections[0]
    attempt = await PublicationJournal(queue).attempt(owner, binding.manifest_id)
    embedded = Checkpoint.model_validate(attempt["embedded_checkpoint"])
    with pytest.raises(DomainError):
        await read_artifact(queue, owner, embedded, binding.source)
    permit = await capture.source_permit(job, binding.source)
    content = await read_artifact(queue, owner, embedded, binding.source, backup=permit)
    assert content and binding.source.version_id == source.version_id
    from domain_kg.operations.capture import authorize_backup_read

    other = await queue.policy.resolve_access(team[2]["owner"], [team[-1]], Action.BACKUP)
    for context in (other, owner.model_copy(update={"action": Action.READ})):
        with pytest.raises(DomainError):
            await authorize_backup_read(queue, context, binding.source, permit)
    with pytest.raises(DomainError):
        await capture.source_permit(job, binding.source.model_copy(update={"version_id": uuid4()}))
    purge = PurgeMaterial(
        kg_id=source.kg_id,
        material_id=source.material_id,
        expected_revision=removed.revision,
        reason="Expired synthetic source",
        idempotency_key="capture-blocks-purge",
    )
    with pytest.raises(DomainError) as protected:
        await lifecycle.purge(owner, purge)
    assert protected.value.code == ErrorCode.REVISION_NOT_READY
    await capture.release(job)
    with pytest.raises(DomainError):
        await read_artifact(queue, owner, embedded, binding.source, backup=permit)
    assert (await lifecycle.purge(owner, purge)).state == "pending"


async def test_capture_guard_rejects_changed_visibility_and_cancelled_lease(publishing, team):
    from domain_kg.operations.capture import BackupCapture

    materials, editor, queue, coordinator, _, _ = publishing
    source, original_job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, original_job, checkpoint))
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.BACKUP)
    receipt = await OwnerOperations(queue).submit(
        owner,
        BackupCommand(
            kg_id=source.kg_id,
            expected_published_revision=1,
            idempotency_key="capture-visibility-race",
        ),
    )
    job = await queue.claim("capture-race", kinds=("backup",))
    capture = BackupCapture(queue)
    await capture.start(job)
    await MaterialLifecycle(materials, queue).remove(
        editor,
        RemoveMaterial(
            kg_id=source.kg_id,
            material_id=source.material_id,
            action="exclude",
            expected_revision=1,
            idempotency_key="change-during-capture",
        ),
    )
    with pytest.raises(DomainError) as changed:
        await capture.guard(job)
    assert changed.value.code == ErrorCode.VERSION_CONFLICT
    await queue.cancel(owner, receipt.job_id, idempotency_key="cancel-active-capture")
    with pytest.raises(DomainError):
        await capture.guard(job)
    await capture.release(job)
    assert not await queue.database.read_pins.find_one(
        {
            "org_id": str(owner.org_id),
            "kg_id": str(source.kg_id),
            "job_id": str(job.id),
            "kind": "backup_capture",
        }
    )


async def test_capture_heartbeat_renews_live_pins_without_reviving_expired_pins(
    material_service, team
):
    from domain_kg.jobs.queue import JobQueue, now
    from domain_kg.operations.capture import BackupCapture

    materials, kg = material_service
    queue = JobQueue(team[1], materials.router)
    await queue.initialize()
    owner = await team[1].resolve_access(team[2]["owner"], [kg], Action.BACKUP)
    await OwnerOperations(queue).submit(
        owner,
        BackupCommand(
            kg_id=kg, expected_published_revision=0, idempotency_key="backup-pin-heartbeat"
        ),
    )
    job = await queue.claim("capture-heartbeat", kinds=("backup",))
    capture = BackupCapture(queue)
    await capture.start(job)
    key = {
        "org_id": str(owner.org_id),
        "kg_id": str(kg),
        "kind": "backup_capture",
        "job_id": str(job.id),
        "attempt_id": str(job.attempt_id),
    }
    await queue.database.read_pins.update_one(
        key, {"$set": {"expires_at": now() + timedelta(seconds=1)}}
    )
    await queue.heartbeat(job)
    row = await queue.database.read_pins.find_one(key)
    assert row["expires_at"].replace(tzinfo=UTC) > now() + timedelta(seconds=10)
    await capture.guard(job)
    await queue.database.read_pins.update_one(
        key, {"$set": {"expires_at": now() - timedelta(seconds=1)}}
    )
    await queue.heartbeat(job)
    with pytest.raises(DomainError) as expired:
        await capture.guard(job)
    assert expired.value.code == ErrorCode.REVISION_NOT_READY
    await capture.release(job)
