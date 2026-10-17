"""Storage changes and lease cancellation stop cleanup without erasing its history."""

import pytest

from domain_kg.errors import DomainError
from domain_kg.identity.models import Action
from domain_kg.jobs.queue import JobQueue
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import RetentionCommand
from domain_kg.operations.retention import RetentionService

pytestmark = pytest.mark.storage


async def test_configuration_change_fences_execution_but_preserves_owner_job_inspection(
    material_service, team
):
    materials, kg = material_service
    queue = JobQueue(team[1], materials.router, lease_seconds=120)
    await queue.initialize()
    owner = await queue.policy.resolve_access(team[2]["owner"], [kg], Action.CONFIGURE)
    receipt = await OwnerOperations(queue).submit(
        owner,
        RetentionCommand(
            kg_id=kg,
            expected_published_revision=0,
            resources="both",
            idempotency_key="configuration-fence",
        ),
    )
    job = await queue.claim("retention", kinds=("retention",))
    await queue.database.kgs.update_one(
        {"org_id": str(owner.org_id), "id": str(kg)}, {"$inc": {"configuration_epoch": 1}}
    )
    # Inspection and cancellation are still authorized owner operations; the
    # historical plan must not disappear merely because execution is now fenced.
    assert (await queue.get(owner, receipt.job_id)).job_id == receipt.job_id
    with pytest.raises(DomainError):
        await RetentionService(queue).run(job)
    assert not await queue.database.retention_results.find_one({"id": str(job.target_id)})
