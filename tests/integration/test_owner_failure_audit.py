"""A real lease logs owner-operation failure atomically even after owner revocation."""

import pytest

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.jobs.queue import JobQueue
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import BackupCommand

pytestmark = pytest.mark.storage


@pytest.mark.parametrize("withdrawn", [False, True])
async def test_owner_failure_audit_uses_admitted_actor_without_current_grant_impersonation(
    material_service, team, withdrawn
):
    materials, kg = material_service
    queue = JobQueue(team[1], materials.router, lease_seconds=120)
    await queue.initialize()
    owner = await queue.policy.resolve_access(team[2]["owner"], [kg], Action.BACKUP)
    receipt = await OwnerOperations(queue).submit(
        owner,
        BackupCommand(
            kg_id=kg, expected_published_revision=0, idempotency_key="failing-owner-backup"
        ),
    )
    job = await queue.claim("owner-failure", kinds=("backup",))
    if withdrawn:
        await queue.database.members.update_one(
            {"org_id": str(owner.org_id), "id": str(team[3]["owner"].id)},
            {"$set": {"suspended": True}},
        )
    await queue.fail(
        job,
        DomainError(ErrorCode.VALIDATION_ERROR, "private error text with endpoint and credentials"),
    )
    events = await queue.database.operation_audit.find(
        {
            "org_id": str(owner.org_id),
            "kg_id": str(kg),
            "job_id": str(receipt.job_id),
            "outcome": "failed",
        }
    ).to_list()
    assert len(events) == 1
    assert events[0]["attempt_id"] == str(job.attempt_id)
    assert events[0]["actor_id"] == str(team[3]["owner"].id)
    assert events[0]["error_code"] == "VALIDATION_ERROR" and "private error" not in str(events)
    with pytest.raises(DomainError):
        await queue.fail(job, DomainError(ErrorCode.VALIDATION_ERROR, "same attempt"))
    assert (
        await queue.database.operation_audit.count_documents(
            {"job_id": str(job.id), "outcome": "failed"}
        )
        == 1
    )


async def test_expired_exhausted_owner_lease_is_audited_once(material_service, team):
    from datetime import timedelta

    from domain_kg.jobs.queue import now

    materials, kg = material_service
    queue = JobQueue(team[1], materials.router, lease_seconds=120)
    await queue.initialize()
    owner = await queue.policy.resolve_access(team[2]["owner"], [kg], Action.BACKUP)
    await OwnerOperations(queue).submit(
        owner,
        BackupCommand(
            kg_id=kg, expected_published_revision=0, idempotency_key="exhausted-owner-backup"
        ),
    )
    job = await queue.claim("last-attempt", kinds=("backup",))
    await queue.database.jobs.update_one(
        queue._scope(job),
        {"$set": {"attempts": queue.max_attempts, "lease_until": now() - timedelta(seconds=1)}},
    )
    assert await queue.claim("expiry-sweep", kinds=("backup",)) is None
    assert await queue.claim("repeat-expiry-sweep", kinds=("backup",)) is None
    events = await queue.database.operation_audit.find(
        {"job_id": str(job.id), "outcome": "failed"}
    ).to_list()
    assert len(events) == 1 and events[0]["failure_reason"] == "lease_expired"
    assert events[0]["attempt_id"] == str(job.attempt_id)
    assert (await queue.get(owner, job.id)).stage == "failed"


async def test_failure_audit_insert_error_rolls_back_job_transition(
    material_service, team, monkeypatch
):
    from pymongo.asynchronous.collection import AsyncCollection

    materials, kg = material_service
    queue = JobQueue(team[1], materials.router, lease_seconds=120)
    await queue.initialize()
    owner = await queue.policy.resolve_access(team[2]["owner"], [kg], Action.BACKUP)
    await OwnerOperations(queue).submit(
        owner,
        BackupCommand(
            kg_id=kg, expected_published_revision=0, idempotency_key="failure-audit-atomicity"
        ),
    )
    job = await queue.claim("audit-rollback", kinds=("backup",))
    insert = AsyncCollection.insert_one

    async def fail_insert(self, document, *args, **kwargs):
        if self.name == "operation_audit" and document.get("outcome") == "failed":
            raise RuntimeError("Synthetic audit interruption.")
        return await insert(self, document, *args, **kwargs)

    monkeypatch.setattr(AsyncCollection, "insert_one", fail_insert)
    with pytest.raises(RuntimeError):
        await queue.fail(job, DomainError(ErrorCode.VALIDATION_ERROR, "synthetic failure"))
    assert (await queue.get(owner, job.id)).stage == "validating"
    monkeypatch.setattr(AsyncCollection, "insert_one", insert)
    await queue.fail(job, DomainError(ErrorCode.VALIDATION_ERROR, "synthetic failure"))
    assert (await queue.get(owner, job.id)).stage == "failed"
