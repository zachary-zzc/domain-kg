"""Owner maintenance work has genuine scoped jobs, never fabricated source versions."""

from datetime import timedelta
from uuid import uuid4

import pytest

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.jobs.queue import JobQueue, now

pytestmark = pytest.mark.storage


async def test_owner_operation_admission_replay_controls_and_current_actor(material_service, team):
    from domain_kg.operations.jobs import OwnerOperations
    from domain_kg.operations.models import BackupCommand

    materials, kg = material_service
    _, policy, principals, members, org, _ = team
    queue = JobQueue(policy, materials.router)
    await queue.initialize()
    operations = OwnerOperations(queue)
    await operations.initialize()
    owner = await policy.resolve_access(principals["owner"], [kg], Action.BACKUP)
    editor = await policy.resolve_access(principals["editor"], [kg], Action.INGEST)
    command = BackupCommand(
        kg_id=kg, expected_published_revision=0, idempotency_key="owner-backup-job"
    )
    with pytest.raises(DomainError):
        await operations.submit(editor, command)
    receipt = await operations.submit(owner, command)
    assert receipt.stage == "queued" and receipt.kg_id == kg
    assert await operations.submit(owner, command) == receipt
    with pytest.raises(DomainError) as conflict:
        await operations.submit(owner, command.model_copy(update={"retention_days": 8}))
    assert conflict.value.code == ErrorCode.IDEMPOTENCY_CONFLICT
    raw = await queue.database.jobs.find_one(
        {"org_id": str(org), "kg_id": str(kg), "id": str(receipt.job_id)}
    )
    assert "material_id" not in raw and "source_version_id" not in raw
    assert await queue.get(owner, receipt.job_id) == receipt
    with pytest.raises(DomainError):
        await queue.get(editor, receipt.job_id)
    assert (
        await queue.cancel(owner, receipt.job_id, idempotency_key="cancel-owner-op")
    ).stage == "cancelled"
    assert (
        await queue.retry(owner, receipt.job_id, idempotency_key="retry-owner-op")
    ).stage == "queued"
    leased = await queue.claim("owner-operation-worker", kinds=("backup",))
    assert leased and leased.id == receipt.job_id
    assert (await queue.authorize(leased)).principal == owner.principal
    with pytest.raises(DomainError) as incomplete:
        await queue.finish(leased)
    assert incomplete.value.code == ErrorCode.REVISION_NOT_READY
    await queue.database.members.update_one(
        {"org_id": str(org), "id": str(members["owner"].id)}, {"$set": {"suspended": True}}
    )
    with pytest.raises(DomainError):
        await queue.authorize(leased)
    await queue.database.jobs.update_one(
        {"org_id": str(org), "kg_id": str(kg), "id": str(leased.id)},
        {"$set": {"lease_until": now() - timedelta(seconds=1)}},
    )
    successor = await queue.claim("owner-op-recovery", kinds=("backup",))
    assert successor and successor.attempt_id != leased.attempt_id
    with pytest.raises(DomainError):
        await queue.authorize(leased)


async def test_owner_operation_reference_corruption_is_not_a_source_or_hidden_row(
    material_service, team
):
    from domain_kg.operations.jobs import OwnerOperations
    from domain_kg.operations.models import BackupCommand

    materials, kg = material_service
    queue = JobQueue(team[1], materials.router)
    await queue.initialize()
    operations = OwnerOperations(queue)
    await operations.initialize()
    owner = await team[1].resolve_access(team[2]["owner"], [kg], Action.BACKUP)
    receipt = await operations.submit(
        owner,
        BackupCommand(
            kg_id=kg, expected_published_revision=0, idempotency_key="corrupt-op-reference"
        ),
    )
    await queue.database.jobs.update_one(
        {"org_id": str(owner.org_id), "kg_id": str(kg), "id": str(receipt.job_id)},
        {"$set": {"target_id": str(uuid4())}},
    )
    with pytest.raises(DomainError) as corrupted:
        await queue.get(owner, receipt.job_id)
    assert corrupted.value.code == ErrorCode.PROJECTION_INCONSISTENT
