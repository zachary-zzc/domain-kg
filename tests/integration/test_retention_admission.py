"""Cleanup uses a current-owner job and immutable policy, not a fake source version."""

import pytest

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.jobs.queue import JobQueue
from domain_kg.operations.jobs import OwnerOperations

pytestmark = pytest.mark.storage


async def test_retention_admission_owner_replay_and_worker_isolation(material_service, team):
    from domain_kg.operations.models import RetentionCommand

    materials, kg = material_service
    queue = JobQueue(team[1], materials.router, lease_seconds=120)
    await queue.initialize()
    operations = OwnerOperations(queue)
    owner = await queue.policy.resolve_access(team[2]["owner"], [kg], Action.CONFIGURE)
    command = RetentionCommand(
        kg_id=kg,
        expected_published_revision=0,
        resources="exports",
        idempotency_key="owner-expired-export-cleanup",
    )
    for role in ("editor", "reader"):
        actor = await queue.policy.resolve_access(team[2][role], [kg], Action.READ)
        with pytest.raises(DomainError):
            await operations.submit(actor, command)
    with pytest.raises(DomainError) as stale:
        await operations.submit(
            owner,
            command.model_copy(
                update={"expected_published_revision": 1, "idempotency_key": "stale-cleanup"}
            ),
        )
    assert stale.value.code == ErrorCode.VERSION_CONFLICT
    receipt = await operations.submit(owner, command)
    assert await operations.submit(owner, command) == receipt
    target = await queue.database.retention_plans.find_one(
        {"org_id": str(owner.org_id), "kg_id": str(kg), "job.id": str(receipt.job_id)}
    )
    assert target is not None and target["policy"]["grace_seconds"] == 86400
    assert target["resources"] == "exports" and target["targets"] == []
    assert await queue.claim("ordinary-source-worker") is None
    job = await queue.claim("cleanup-only", kinds=("retention",))
    assert job.id == receipt.job_id
    assert not await queue.accepted(job)
    with pytest.raises(DomainError):
        await queue.finish(job)

    from domain_kg.operations.retention import RetentionService

    await RetentionService(queue).run(job)
    assert await queue.accepted(job)
    await queue.finish(job)
