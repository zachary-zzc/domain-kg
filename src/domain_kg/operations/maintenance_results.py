"""Authenticate accepted repair lineage for current readers without an old lease."""

from typing import Any

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import AccessContext, Action, Principal
from domain_kg.jobs.models import JobCommand, LeasedJob
from domain_kg.jobs.queue import JobQueue
from domain_kg.operations.maintenance_origin import MaintenanceOrigin, MaintenanceResult
from domain_kg.operations.maintenance_targets import MaintenanceTargets
from domain_kg.operations.models import OwnerOperation
from domain_kg.revisions.journal import PublicationJournal
from domain_kg.revisions.projections import digest


def invalid() -> DomainError:
    return DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Accepted repair provenance differs.")


async def maintenance_base(
    queue: JobQueue, access: AccessContext, attempt: dict[str, Any]
) -> dict[str, Any]:
    current = await queue.policy.revalidate(access)
    origin = MaintenanceOrigin.model_validate(attempt["maintenance_origin"])
    if origin.org_id != current.org_id or origin.kg_id not in current.kg_ids:
        raise invalid()
    ns = {"org_id": str(origin.org_id), "kg_id": str(origin.kg_id)}
    raw = await queue.database.maintenance_results.find_one(
        ns | {"id": str(origin.id), "state": "sealed"}
    )
    if raw is None:
        raise invalid()
    result = MaintenanceResult.model_validate(
        {key: raw[key] for key in MaintenanceResult.model_fields}
    )
    job = await queue.database.jobs.find_one(
        ns | {"id": str(origin.job_id), "kind": "maintenance", "stage": "ready"}
    )
    operation = await queue.database.owner_operations.find_one(ns | {"id": str(origin.id)})
    if job is None or operation is None:
        raise invalid()
    record = OwnerOperation.model_validate(
        {key: value for key, value in operation.items() if key != "_id"}
    )
    target = await MaintenanceTargets(queue).record(record)
    if (
        MaintenanceOrigin.model_validate(
            result.model_dump(include=set(MaintenanceOrigin.model_fields))
        )
        != origin
        or target.base != origin.base
        or target.parent.manifest_id != origin.parent_manifest_id
        or record.command.kind != "maintenance"
        or record.intent_hash != digest(record.command.model_dump(mode="json"))
        or record.actor.model_dump(mode="json") != job["actor"]
        or record.job.id != origin.job_id
        or job.get("target_id") != str(origin.id)
        or job.get("attempt_id") != str(origin.attempt_id)
        or job.get("accepted_operation_hash") != digest(result.model_dump(mode="json"))
        or job.get("accepted_operation_id") != str(origin.id)
        or attempt["state"] != "accepted"
        or attempt["id"] != str(origin.manifest_id)
        or attempt["curation"] != result.binding.curation.model_dump(mode="json")  # type: ignore[union-attr]
        or any(
            attempt["batch"][key] != str(getattr(result.binding, key))
            for key in (
                "org_id",
                "kg_id",
                "manifest_id",
                "attempt_id",
                "content_hash",
                "generation",
            )
        )
        or JobCommand.model_validate(
            {key: attempt["job"][key] for key in JobCommand.model_fields if key in attempt["job"]}
        ).id
        != origin.job_id
    ):
        raise invalid()
    root = await queue.database.publication_manifests.find_one(
        ns
        | {
            "id": str(origin.manifest_id),
            "state": "accepted",
            "maintenance_operation_id": str(origin.id),
            "revision": result.published_revision,
        }
    )
    if root is None:
        raise invalid()
    base = await PublicationJournal(queue).attempt(current, origin.base.manifest_id)
    if (
        base.get("maintenance_origin")
        or base["state"] != "accepted"
        or base["embedded_checkpoint"] != attempt["embedded_checkpoint"]
    ):
        raise invalid()
    await queue.policy.revalidate(current)
    return base


async def maintenance_artifact_job(
    queue: JobQueue, access: AccessContext, attempt: dict[str, Any]
) -> JobCommand:
    base = await maintenance_base(queue, access, attempt)
    if base.get("restored_origin"):
        from domain_kg.operations.restored_attempts import restored_job

        return await restored_job(queue, access, base)
    return JobCommand.model_validate(
        {key: base["job"][key] for key in JobCommand.model_fields if key in base["job"]}
    )


class MaintenanceResults:
    def __init__(self, queue: JobQueue) -> None:
        self.queue = queue

    async def accepted(self, job: LeasedJob) -> bool:
        raw = await self.queue.database.jobs.find_one(
            self.queue._scope(job)
            | {
                "kind": "maintenance",
                "stage": "ready",
                "attempt_id": str(job.attempt_id),
                "lease_token": str(job.lease_token),
                "fence": job.fence,
            }
        )
        if raw is None:
            return False
        access = await self.queue.policy.resolve_access(
            Principal.model_validate(raw["actor"]), [job.kg_id], Action.CONFIGURE, org_id=job.org_id
        )
        attempt = await PublicationJournal(self.queue).attempt(access, raw["accepted_manifest_id"])
        await maintenance_base(self.queue, access, attempt)
        return True
