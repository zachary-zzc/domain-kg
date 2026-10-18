"""Freeze expired, sealed package descriptors under an actual owner cleanup job."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.jobs.queue import JobQueue, now
from domain_kg.materials.service import digest
from domain_kg.models import SHA256, FrozenModel, ScopedRef, UTCDateTime
from domain_kg.operations.backup_models import BackupSeal
from domain_kg.operations.export_models import ExportManifest
from domain_kg.operations.generation_collection import (
    GenerationCollection,
    GenerationCollectionTarget,
)
from domain_kg.operations.incomplete_backups import IncompleteBackups, IncompleteBackupTarget
from domain_kg.operations.models import OwnerOperation, RetentionCommand
from domain_kg.operations.retention_policy import RetentionPolicy


def invalid() -> DomainError:
    return DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Cleanup package ownership differs.")


class RetainedTarget(FrozenModel):
    kind: Literal["backup", "export"]
    operation_id: UUID
    job_id: UUID
    attempt_id: UUID
    descriptor: BackupSeal | ExportManifest
    content_hash: SHA256


class RetentionPlan(ScopedRef):
    job: ScopedRef
    storage_profile_id: UUID
    resources: Literal["backups", "exports", "both", "incomplete_backups", "generations"]
    policy: RetentionPolicy
    created_at: UTCDateTime
    cutoff: UTCDateTime
    targets: Annotated[
        tuple[RetainedTarget | IncompleteBackupTarget | GenerationCollectionTarget, ...],
        Field(max_length=100),
    ]

    @model_validator(mode="after")
    def exact_targets(self) -> Self:
        if (self.job.org_id, self.job.kg_id) != (self.org_id, self.kg_id) or (
            self.cutoff != self.policy.cutoff(self.created_at)
            or len(self.targets) > self.policy.max_targets
            or len(
                {
                    (
                        t.kind,
                        t.claim_operation_id
                        if isinstance(t, IncompleteBackupTarget)
                        else t.operation_id,
                    )
                    for t in self.targets
                }
            )
            != len(self.targets)
        ):
            raise ValueError("cleanup boundary differs")
        for target in self.targets:
            if isinstance(target, GenerationCollectionTarget):
                if (
                    self.resources != "generations"
                    or target.settled_at > self.cutoff
                    or target.storage_profile_id != self.storage_profile_id
                ):
                    raise ValueError("collection requires a retired same-placement generation")
                continue
            if isinstance(target, IncompleteBackupTarget):
                if (
                    self.resources != "incomplete_backups"
                    or target.settled_at > self.cutoff
                    or not 1 <= target.object_count <= self.policy.max_objects_per_target
                ):
                    raise ValueError("incomplete cleanup requires a stopped, settled capture")
                continue
            seal = target.descriptor
            if isinstance(seal, BackupSeal):
                ref = seal.manifest
                identity = (ref.org_id, ref.kg_id, ref.backup_id, ref.attempt_id)
                checksum = ref.sha256
                expected_kind = "backup"
            else:
                identity = (seal.org_id, seal.kg_id, seal.export_id, seal.attempt_id)
                checksum = digest(seal.model_dump(mode="json"))
                expected_kind = "export"
            if (
                identity != (self.org_id, self.kg_id, target.operation_id, target.attempt_id)
                or seal.job_id != target.job_id
                or checksum != target.content_hash
                or target.kind != expected_kind
                or (self.resources != "both" and self.resources != expected_kind + "s")
                or seal.expires_at > self.cutoff
            ):
                raise ValueError("cleanup cannot own a live or foreign package")
        return self


class RetentionTargets:
    def __init__(self, queue: JobQueue, policy: RetentionPolicy | None = None) -> None:
        self.queue, self.database = queue, queue.database
        self.policy = policy or RetentionPolicy()

    async def initialize(self) -> None:
        for table in ("retention_plans", "retention_results"):
            await self.database[table].create_index(
                [("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True
            )
        await self.database.retention_claims.create_index(
            [("org_id", 1), ("kg_id", 1), ("kind", 1), ("operation_id", 1)], unique=True
        )

    async def allocate(
        self, operation: OwnerOperation, session: AsyncClientSession
    ) -> RetentionPlan:
        command = operation.command
        if not isinstance(command, RetentionCommand):
            raise inaccessible_reference()
        created = now()
        ns = {"org_id": str(operation.org_id), "kg_id": str(operation.kg_id)}
        targets: list[RetainedTarget | IncompleteBackupTarget | GenerationCollectionTarget] = []
        if command.resources == "generations":
            targets.extend(
                await GenerationCollection(self.queue).allocate(
                    operation,
                    self.policy,
                    created,
                    session,
                )
            )
        if command.resources == "incomplete_backups":
            targets.extend(
                await IncompleteBackups(self.queue).allocate(
                    operation,
                    self.policy,
                    created,
                    session,
                )
            )
        for kind in ("backup", "export"):
            if command.resources not in ("both", kind + "s"):
                continue
            remaining = self.policy.max_targets - len(targets)
            if not remaining:
                break
            # All descriptors are server-created canonical UTC strings. Validate their
            # typed timestamps again; the database predicate alone grants no deletion.
            rows = (
                self.database[kind + "_results"]
                .find(
                    ns
                    | {
                        "state": "sealed",
                        "cleanup_job_id": {"$exists": False},
                        "expires_at": {
                            "$lte": self.policy.cutoff(created).isoformat().replace("+00:00", "Z")
                        },
                    },
                    session=session,
                )
                .sort([("expires_at", 1), ("id", 1)])
                .limit(self.policy.max_targets)
            )
            async for row in rows:
                claim = ns | {"kind": kind, "operation_id": row["id"]}
                if await self.database.retention_claims.find_one(claim, session=session):
                    continue
                model = BackupSeal if kind == "backup" else ExportManifest
                seal = model.model_validate({key: row[key] for key in model.model_fields})
                attempt = (
                    seal.manifest.attempt_id if isinstance(seal, BackupSeal) else seal.attempt_id
                )
                checksum = (
                    seal.manifest.sha256
                    if isinstance(seal, BackupSeal)
                    else digest(seal.model_dump(mode="json"))
                )
                target = RetainedTarget(
                    kind=kind,
                    operation_id=row["id"],
                    job_id=seal.job_id,
                    attempt_id=attempt,
                    descriptor=seal,
                    content_hash=checksum,
                )
                await self.validate(operation, target, session)
                targets.append(target)
                await self.database.retention_claims.insert_one(
                    claim | {"cleanup_job_id": str(operation.job.id), "attempt_id": str(attempt)},
                    session=session,
                )
                changed = await self.database[kind + "_results"].update_one(
                    ns | {"id": row["id"], "cleanup_job_id": {"$exists": False}},
                    {"$set": {"cleanup_job_id": str(operation.job.id)}},
                    session=session,
                )
                if not changed.matched_count:
                    raise invalid()
                if len(targets) == self.policy.max_targets:
                    break
        kg = await self.database.kgs.find_one(
            {"org_id": ns["org_id"], "id": ns["kg_id"]}, session=session
        )
        if kg is None:
            raise inaccessible_reference()
        plan = RetentionPlan(
            org_id=operation.org_id,
            kg_id=operation.kg_id,
            id=operation.id,
            job=operation.job,
            storage_profile_id=kg["storage_profile_id"],
            resources=command.resources,
            policy=self.policy,
            created_at=created,
            cutoff=self.policy.cutoff(created),
            targets=tuple(targets),
        )
        await self.database.retention_plans.insert_one(
            plan.model_dump(mode="json"), session=session
        )
        return plan

    async def record(
        self, operation: OwnerOperation, session: AsyncClientSession | None = None
    ) -> RetentionPlan:
        command = operation.command
        if not isinstance(command, RetentionCommand):
            raise inaccessible_reference()
        row = await self.database.retention_plans.find_one(
            {
                "org_id": str(operation.org_id),
                "kg_id": str(operation.kg_id),
                "id": str(operation.id),
            },
            session=session,
        )
        if row is None:
            raise invalid()
        plan = RetentionPlan.model_validate({key: row[key] for key in RetentionPlan.model_fields})
        if plan.job != operation.job or plan.resources != command.resources:
            raise invalid()
        return plan

    async def validate(
        self,
        operation: OwnerOperation,
        target: RetainedTarget | IncompleteBackupTarget | GenerationCollectionTarget,
        session: AsyncClientSession | None = None,
    ) -> None:
        if isinstance(target, GenerationCollectionTarget):
            await GenerationCollection(self.queue).validate(operation, target, session)
            return
        if isinstance(target, IncompleteBackupTarget):
            await IncompleteBackups(self.queue).validate(operation, target, session)
            return
        ns = {"org_id": str(operation.org_id), "kg_id": str(operation.kg_id)}
        row = await self.database.jobs.find_one(
            ns
            | {
                "id": str(target.job_id),
                "kind": target.kind,
                "target_id": str(target.operation_id),
                "attempt_id": str(target.attempt_id),
                "stage": "ready",
                "accepted_operation_id": str(target.operation_id),
                "accepted_operation_hash": target.content_hash,
            },
            session=session,
        )
        raw = await self.database[target.kind + "_results"].find_one(
            ns | {"id": str(target.operation_id), "state": "sealed"}, session=session
        )
        if (
            row is None
            or raw is None
            or any(
                raw.get(key) != value
                for key, value in target.descriptor.model_dump(mode="json").items()
            )
        ):
            raise invalid()
        admitted = await self.database.owner_operations.find_one(
            ns | {"id": str(target.operation_id)}, session=session
        )
        if admitted is None:
            raise invalid()
        original = OwnerOperation.model_validate(
            {key: admitted[key] for key in OwnerOperation.model_fields}
        )
        if (
            original.job.id,
            original.command.kind,
            original.snapshot,
            original.intent_hash,
            original.actor.model_dump(mode="json"),
        ) != (
            target.job_id,
            target.kind,
            target.descriptor.snapshot,
            digest(original.command.model_dump(mode="json")),
            row["actor"],
        ):
            raise invalid()
        if original.snapshot.configuration_epoch != operation.snapshot.configuration_epoch:
            raise invalid()
