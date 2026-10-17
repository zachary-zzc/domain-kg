"""Current-owner reads of durable, hash-bound restore completion records."""

from typing import Self
from uuid import UUID

from pydantic import model_validator

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import AccessContext, Action, Principal
from domain_kg.jobs.models import LeasedJob
from domain_kg.jobs.queue import JobQueue
from domain_kg.models import SHA256, Namespace, ScopedRef, UTCDateTime
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.revisions.projections import digest


class RestoreResult(ScopedRef):
    job_id: UUID
    attempt_id: UUID
    target: Namespace
    generation: UUID
    root_manifest_id: UUID
    source_backup_id: UUID
    source_header_sha: SHA256
    parts_hash: SHA256
    created_at: UTCDateTime

    @model_validator(mode="after")
    def fresh_same_org_target(self) -> Self:
        if self.target.org_id != self.org_id or self.target.kg_id == self.kg_id:
            raise ValueError("restore completion requires its distinct same-org target")
        return self


class RestoreResults:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database = queue, queue.database

    async def get(self, access: AccessContext, kg: UUID, job_id: UUID) -> RestoreResult:
        current = await self.queue.policy.revalidate(access, action=Action.RESTORE)
        if kg not in current.kg_ids:
            raise DomainError(ErrorCode.RESOURCE_NOT_ACCESSIBLE, "The resource is not accessible.")
        ns = {"org_id": str(current.org_id), "kg_id": str(kg)}
        job = await self.database.jobs.find_one(
            ns | {"id": str(job_id), "kind": "restore", "stage": "ready"}
        )
        if job is None:
            raise DomainError(ErrorCode.REVISION_NOT_READY, "No completed restore is available.")
        operation = await OwnerOperations(self.queue).authorize(current, job)
        row = await self.database.restore_results.find_one(
            ns | {"id": str(operation.id), "state": "sealed"}
        )
        if row is None:
            raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Restore completion is missing.")
        result = RestoreResult.model_validate({key: row[key] for key in RestoreResult.model_fields})
        if (
            result.job_id,
            result.attempt_id,
            str(result.id),
            digest(result.model_dump(mode="json")),
        ) != (
            job_id,
            UUID(job["attempt_id"]),
            job.get("accepted_operation_id"),
            job.get("accepted_operation_hash"),
        ):
            raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Restore completion seal differs.")
        header = await self.database.publication_manifests.find_one(
            {
                "org_id": str(result.target.org_id),
                "kg_id": str(result.target.kg_id),
                "id": str(result.root_manifest_id),
                "state": "accepted",
                "generation": str(result.generation),
                "restore_operation_id": str(result.id),
            }
        )
        if header is None:
            raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Restored publication is missing.")
        await self.queue.policy.revalidate(current, action=Action.RESTORE)
        return result

    async def accepted(self, job: LeasedJob) -> bool:
        raw = await self.database.jobs.find_one(
            self.queue._scope(job)
            | {
                "kind": "restore",
                "stage": "ready",
                "attempt_id": str(job.attempt_id),
                "lease_token": str(job.lease_token),
                "fence": job.fence,
            }
        )
        if raw is None:
            return False
        access = await self.queue.policy.resolve_access(
            Principal.model_validate(raw["actor"]), [job.kg_id], Action.RESTORE, org_id=job.org_id
        )
        result = await self.get(access, job.kg_id, job.id)
        return result.id == job.target_id
