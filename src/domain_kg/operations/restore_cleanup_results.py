"""A cleanup is accepted only by its exact fenced result and immutable target seal."""

from domain_kg.identity.models import Action, Principal
from domain_kg.jobs.models import LeasedJob
from domain_kg.jobs.queue import JobQueue
from domain_kg.materials.service import digest
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.restore_cleanup_targets import RestoreCleanupTargets, invalid


class RestoreCleanupResults:
    def __init__(self, queue: JobQueue) -> None:
        self.queue = queue

    async def accepted(self, job: LeasedJob) -> bool:
        row = await self.queue.database.jobs.find_one(
            self.queue._scope(job)
            | {
                "kind": "restore_cleanup",
                "stage": "ready",
                "attempt_id": str(job.attempt_id),
                "accepted_operation_id": str(job.target_id),
            }
        )
        if row is None:
            return False
        access = await self.queue.policy.resolve_access(
            Principal.model_validate(row["actor"]), [job.kg_id], Action.RESTORE, org_id=job.org_id
        )
        operation = await OwnerOperations(self.queue).authorize(access, row)
        target = await RestoreCleanupTargets(self.queue).record(operation)
        checksum = digest(target.model_dump(mode="json"))
        result = await self.queue.database.restore_cleanup_results.find_one(
            {
                "org_id": str(job.org_id),
                "kg_id": str(job.kg_id),
                "id": str(job.target_id),
                "job_id": str(job.id),
                "attempt_id": str(job.attempt_id),
                "state": "completed",
                "target_hash": checksum,
            }
        )
        if result is None or row.get("accepted_operation_hash") != checksum:
            raise invalid()
        await self.queue.policy.revalidate(access, action=Action.RESTORE)
        return True
