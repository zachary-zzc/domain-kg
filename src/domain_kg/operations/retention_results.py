"""Completion requires the exact owner plan and a fenced cleanup result."""

from pydantic import ValidationError

from domain_kg.identity.models import Principal
from domain_kg.jobs.models import LeasedJob
from domain_kg.jobs.queue import JobQueue
from domain_kg.materials.service import digest
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.retention_targets import RetentionTargets, invalid


class RetentionResults:
    def __init__(self, queue: JobQueue) -> None:
        self.queue = queue

    async def accepted(self, job: LeasedJob) -> bool:
        raw = await self.queue.database.jobs.find_one(
            self.queue._scope(job)
            | {
                "kind": "retention",
                "stage": "ready",
                "attempt_id": str(job.attempt_id),
                "accepted_operation_id": str(job.target_id),
            }
        )
        if raw is None:
            return False
        access = await self.queue.policy.resolve_access(
            # The admitted actor remains the caller; no historical source actor is used.
            Principal.model_validate(raw["actor"]),
            [job.kg_id],
            self.queue._action(job.kind),
            org_id=job.org_id,
        )
        operation = await OwnerOperations(self.queue).authorize(access, raw)
        try:
            plan = await RetentionTargets(self.queue).record(operation)
            row = await self.queue.database.retention_results.find_one(
                {
                    "org_id": str(job.org_id),
                    "kg_id": str(job.kg_id),
                    "id": str(job.target_id),
                    "job_id": str(job.id),
                    "attempt_id": str(job.attempt_id),
                    "state": "completed",
                }
            )
            checksum = digest(plan.model_dump(mode="json"))
            if (
                row is None
                or row.get("plan_hash") != checksum
                or row.get("target_count") != len(plan.targets)
                or raw.get("accepted_operation_hash") != checksum
            ):
                raise invalid()
        except (ValidationError, KeyError, ValueError, TypeError):
            raise invalid() from None
        await self.queue.policy.revalidate(access, action=access.action)
        return True
