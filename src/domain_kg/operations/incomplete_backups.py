"""Freeze only fully acknowledged failed backup attempts, never live or unknown writes."""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.jobs.queue import JobQueue
from domain_kg.materials.service import digest as command_digest
from domain_kg.models import SHA256, FrozenModel, NonNegative, UTCDateTime
from domain_kg.operations.backup_models import BackupSeal
from domain_kg.operations.models import OwnerOperation
from domain_kg.operations.packages import PackageObject
from domain_kg.operations.retention_policy import RetentionPolicy
from domain_kg.revisions.projections import digest


def invalid() -> DomainError:
    return DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Incomplete backup ownership differs.")


class IncompleteBackupTarget(FrozenModel):
    kind: Literal["incomplete_backup"] = "incomplete_backup"
    operation_id: UUID
    job_id: UUID
    attempt_id: UUID
    attempt_record_id: UUID
    intent_hash: SHA256
    write_epoch: NonNegative
    object_count: NonNegative
    objects_hash: SHA256
    settled_at: UTCDateTime

    @property
    def claim_operation_id(self) -> UUID:
        return self.attempt_record_id


def writer_fenced(attempt: dict[str, Any], job: dict[str, Any]) -> bool:
    if job.get("attempt_id") == attempt["attempt_id"]:
        return job.get("stage") in ("failed", "cancelled")
    return (
        type(job.get("fence")) is int
        and type(attempt.get("original_fence")) is int
        and job["fence"] > attempt["original_fence"]
    )


class IncompleteBackups:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database = queue, queue.database

    async def objects(
        self,
        operation: OwnerOperation,
        target: IncompleteBackupTarget,
        session: AsyncClientSession | None = None,
    ) -> tuple[PackageObject, ...]:
        rows = (
            await self.database.backup_writes.find(
                {
                    "org_id": str(operation.org_id),
                    "kg_id": str(operation.kg_id),
                    "attempt_key": str(target.attempt_record_id),
                },
                session=session,
            )
            .sort("id", 1)
            .limit(100001)
            .to_list()
        )
        if not rows or len(rows) > 100000 or any(row.get("state") != "settled" for row in rows):
            raise invalid()
        references = tuple(PackageObject.model_validate(row["reference"]) for row in rows)
        for row, reference in zip(rows, references, strict=True):
            if (
                reference.org_id,
                reference.kg_id,
                reference.backup_id,
                reference.attempt_id,
                str(reference.id),
                row.get("job_id"),
                row.get("operation_id"),
                row.get("attempt_id"),
            ) != (
                operation.org_id,
                operation.kg_id,
                target.operation_id,
                target.attempt_id,
                row["id"],
                str(target.job_id),
                str(target.operation_id),
                str(target.attempt_id),
            ):
                raise invalid()
        if (
            len(references) != target.object_count
            or digest([reference.model_dump(mode="json") for reference in references])
            != target.objects_hash
        ):
            raise invalid()
        return references

    async def validate(
        self,
        operation: OwnerOperation,
        target: IncompleteBackupTarget,
        session: AsyncClientSession | None = None,
    ) -> None:
        ns = {"org_id": str(operation.org_id), "kg_id": str(operation.kg_id)}
        attempt = await self.database.backup_attempts.find_one(
            ns
            | {
                "id": str(target.attempt_record_id),
                "job_id": str(target.job_id),
                "operation_id": str(target.operation_id),
                "attempt_id": str(target.attempt_id),
                "tracking_version": 1,
                "write_epoch": target.write_epoch,
                "pending_count": 0,
                "uncertain_count": 0,
                "state": "stopped",
            },
            session=session,
        )
        job = await self.database.jobs.find_one(
            ns
            | {
                "id": str(target.job_id),
                "kind": "backup",
                "target_id": str(target.operation_id),
            },
            session=session,
        )
        raw = await self.database.owner_operations.find_one(
            ns | {"id": str(target.operation_id)},
            session=session,
        )
        sealed = await self.database.backup_results.find_one(
            ns | {"id": str(target.operation_id), "state": "sealed"},
            session=session,
        )
        if attempt is None or job is None or raw is None or not writer_fenced(attempt, job):
            raise invalid()
        if sealed is not None:
            seal = BackupSeal.model_validate({k: sealed[k] for k in BackupSeal.model_fields})
            if (
                seal.manifest.org_id,
                seal.manifest.kg_id,
                seal.manifest.backup_id,
                seal.job_id,
            ) != (operation.org_id, operation.kg_id, target.operation_id, target.job_id):
                raise invalid()
            if seal.manifest.attempt_id == target.attempt_id:
                raise invalid()
        if (
            attempt.get("cleanup_job_id") not in (None, str(operation.job.id))
            or attempt["last_settled_at"].replace(tzinfo=UTC) != target.settled_at
        ):
            raise invalid()
        original = OwnerOperation.model_validate({k: raw[k] for k in OwnerOperation.model_fields})
        if (
            original.command.kind != "backup"
            or original.job.id != target.job_id
            or original.intent_hash != target.intent_hash
            or original.intent_hash != command_digest(original.command.model_dump(mode="json"))
            or original.actor.model_dump(mode="json") != job["actor"]
            or original.snapshot.configuration_epoch != operation.snapshot.configuration_epoch
        ):
            raise invalid()

    async def allocate(
        self,
        operation: OwnerOperation,
        policy: RetentionPolicy,
        created: datetime,
        session: AsyncClientSession,
    ) -> tuple[IncompleteBackupTarget, ...]:
        ns = {"org_id": str(operation.org_id), "kg_id": str(operation.kg_id)}
        targets = []
        rows = (
            self.database.backup_attempts.find(
                ns
                | {
                    "tracking_version": 1,
                    "state": "stopped",
                    "pending_count": 0,
                    "uncertain_count": 0,
                    "cleanup_job_id": {"$exists": False},
                    "last_settled_at": {"$lte": policy.cutoff(created)},
                },
                session=session,
            )
            .sort([("last_settled_at", 1), ("id", 1)])
            .limit(100)
        )
        async for row in rows:
            job = await self.database.jobs.find_one(
                ns
                | {
                    "id": row["job_id"],
                    "kind": "backup",
                    "target_id": row["operation_id"],
                },
                session=session,
            )
            writes = (
                await self.database.backup_writes.find(
                    ns | {"attempt_key": row["id"]},
                    session=session,
                )
                .sort("id", 1)
                .limit(policy.max_objects_per_target + 1)
                .to_list()
            )
            if (
                job is None
                or not writer_fenced(row, job)
                or not writes
                or len(writes) > policy.max_objects_per_target
                or any(value.get("state") != "settled" for value in writes)
            ):
                continue
            raw = await self.database.owner_operations.find_one(
                ns | {"id": row["operation_id"]},
                session=session,
            )
            if raw is None:
                raise invalid()
            references = [PackageObject.model_validate(value["reference"]) for value in writes]
            target = IncompleteBackupTarget(
                operation_id=row["operation_id"],
                job_id=row["job_id"],
                attempt_id=row["attempt_id"],
                attempt_record_id=row["id"],
                intent_hash=raw["intent_hash"],
                write_epoch=row["write_epoch"],
                object_count=len(references),
                objects_hash=digest(
                    [reference.model_dump(mode="json") for reference in references]
                ),
                settled_at=row["last_settled_at"].replace(tzinfo=UTC),
            )
            await self.validate(operation, target, session)
            await self.objects(operation, target, session)
            # Serialize cleanup admission with job retry/cancel. Reading the old
            # job and writing only the attempt record would permit write skew.
            changed_job = await self.database.jobs.update_one(
                ns
                | {
                    "id": str(target.job_id),
                    "attempt_id": job["attempt_id"],
                    "fence": job["fence"],
                },
                {"$inc": {"backup_cleanup_epoch": 1}},
                session=session,
            )
            if not changed_job.matched_count:
                raise invalid()
            changed = await self.database.backup_attempts.update_one(
                ns
                | {
                    "id": row["id"],
                    "write_epoch": row["write_epoch"],
                    "cleanup_job_id": {"$exists": False},
                },
                {"$set": {"cleanup_job_id": str(operation.job.id)}},
                session=session,
            )
            if not changed.matched_count:
                raise invalid()
            await self.database.retention_claims.insert_one(
                ns
                | {
                    "kind": target.kind,
                    "operation_id": str(target.claim_operation_id),
                    "attempt_id": str(target.attempt_id),
                    "cleanup_job_id": str(operation.job.id),
                },
                session=session,
            )
            targets.append(target)
            if len(targets) >= policy.max_targets:
                break
        return tuple(targets)
