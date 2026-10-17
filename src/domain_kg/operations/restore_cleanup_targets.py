"""A cleanup freezes one terminal restore's fresh namespace and settled object writes."""

from typing import Annotated, Self
from uuid import UUID

from pydantic import Field, model_validator
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.catalog.models import StorageProfile
from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext
from domain_kg.jobs.queue import JobQueue
from domain_kg.materials.models import BlobRef
from domain_kg.models import Namespace, ScopedRef
from domain_kg.operations.models import OwnerOperation, RestoreCleanupCommand, RestoreCommand
from domain_kg.operations.restore_cleanup_policy import RestoreCleanupPolicy
from domain_kg.operations.restore_quota import RestoreQuota, RestoreQuotaReservation
from domain_kg.operations.restore_targets import RestoreTargets


def invalid() -> DomainError:
    return DomainError(
        ErrorCode.PROJECTION_INCONSISTENT, "Inactive restore cleanup ownership differs."
    )


class RestoreCleanupTarget(ScopedRef):
    job: ScopedRef
    restore_job: ScopedRef
    restore_operation_id: UUID
    restore_attempt_id: UUID | None
    target: Namespace
    placement: StorageProfile
    generation: UUID
    policy: RestoreCleanupPolicy
    objects: Annotated[tuple[BlobRef, ...], Field(max_length=10000)]
    reservation: RestoreQuotaReservation | None

    @model_validator(mode="after")
    def exact_scope(self) -> Self:
        if (
            self.target.org_id != self.org_id
            or self.target.kg_id == self.kg_id
            or self.org_id not in self.placement.allowed_org_ids
            or any(
                (ref.org_id, ref.kg_id) != (self.org_id, self.kg_id)
                for ref in (self.job, self.restore_job)
            )
            or any(
                (ref.org_id, ref.kg_id) != (self.target.org_id, self.target.kg_id)
                for ref in self.objects
            )
            or len(self.objects) > self.policy.max_objects
            or len({(ref.material_id, ref.version_id, ref.object_id) for ref in self.objects})
            != len(self.objects)
        ):
            raise ValueError("cleanup must own a fresh same-organization target")
        quota = self.reservation
        if quota is not None and (
            (quota.org_id, quota.kg_id, quota.id, quota.job_id, quota.target, quota.state)
            != (
                self.org_id,
                self.kg_id,
                self.restore_operation_id,
                self.restore_job.id,
                self.target,
                "reserved",
            )
        ):
            raise ValueError("cleanup must own its exact uncommitted quota reservation")
        return self


class RestoreCleanupTargets:
    def __init__(self, queue: JobQueue, policy: RestoreCleanupPolicy | None = None) -> None:
        self.queue, self.database = queue, queue.database
        self.policy = policy or RestoreCleanupPolicy()

    async def initialize(self) -> None:
        for table in ("restore_cleanup_targets", "restore_cleanup_results"):
            await self.database[table].create_index(
                [("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True
            )

    async def allocate(
        self, access: AccessContext, operation: OwnerOperation, session: AsyncClientSession
    ) -> RestoreCleanupTarget:
        from domain_kg.operations.jobs import OwnerOperations

        command = operation.command
        if not isinstance(command, RestoreCleanupCommand) or operation.actor.kind != "human":
            raise inaccessible_reference()
        ns = {"org_id": str(operation.org_id), "kg_id": str(operation.kg_id)}
        raw_job = await self.database.jobs.find_one(
            ns
            | {
                "id": str(command.restore_job_id),
                "kind": "restore",
                "stage": {"$in": ["failed", "cancelled"]},
            },
            session=session,
        )
        if raw_job is None:
            raise DomainError(
                ErrorCode.VERSION_CONFLICT,
                "Only a failed or cancelled inactive restore can be cleaned.",
            )
        original = await OwnerOperations(self.queue).authorize(access, raw_job, session)
        if not isinstance(original.command, RestoreCommand):
            raise invalid()
        target = await RestoreTargets(self.queue).record(original, session)
        raw_target = await self.database.restore_targets.find_one(
            ns | {"id": str(original.id)}, session=session
        )
        if (
            raw_target is None
            or raw_target.get("write_tracking_version") != 1
            or raw_target.get("cleanup_job_id")
            or target.state not in ("allocated", "preparing", "verified", "abandoned")
        ):
            raise DomainError(
                ErrorCode.VERSION_CONFLICT, "This restore target cannot be cleaned by a new job."
            )
        if await self.database.kgs.find_one(
            {"org_id": str(target.target.org_id), "id": str(target.target.kg_id)}, session=session
        ):
            raise inaccessible_reference()
        references: dict[tuple[UUID, UUID, UUID], BlobRef] = {}
        count = 0
        async for row in self.database.restore_writes.find(
            ns | {"job_id": str(original.job.id)}, session=session
        ).limit(self.policy.max_write_records + 1):
            count += 1
            if count > self.policy.max_write_records:
                raise DomainError(
                    ErrorCode.QUOTA_EXCEEDED, "Restore write inventory exceeds its cleanup bound."
                )
            if row.get("state") in ("pending", "uncertain"):
                raise DomainError(
                    ErrorCode.REVISION_NOT_READY,
                    "Target writes are pending or uncertain; cleanup is blocked.",
                )
            if (
                row.get("state") != "settled"
                or row.get("operation_id") != str(original.id)
                or row.get("target") != target.target.model_dump(mode="json")
            ):
                raise invalid()
            if row.get("kind") == "blob":
                ref = BlobRef.model_validate(row["reference"])
                key = (ref.material_id, ref.version_id, ref.object_id)
                if key in references and references[key] != ref:
                    raise invalid()
                references[key] = ref
            elif (
                row.get("kind") not in ("provision", "graph", "vector")
                or row.get("reference") is not None
            ):
                raise invalid()
        if len(references) > self.policy.max_objects:
            raise DomainError(
                ErrorCode.QUOTA_EXCEEDED, "Restore objects exceed their cleanup bound."
            )
        quota_row = await self.database.restore_quota_reservations.find_one(
            ns | {"id": str(original.id)}, session=session
        )
        quota = RestoreQuota.saved(quota_row) if quota_row else None
        cleanup = RestoreCleanupTarget(
            **operation.model_dump(include={"org_id", "kg_id", "id"}),
            job=operation.job,
            restore_job=original.job,
            restore_operation_id=original.id,
            restore_attempt_id=raw_job.get("attempt_id"),
            target=target.target,
            placement=target.placement,
            generation=target.generation,
            policy=self.policy,
            objects=tuple(sorted(references.values(), key=lambda ref: ref.model_dump_json())),
            reservation=quota,
        )
        if len(cleanup.model_dump_json().encode()) > 8 * 1024**2:
            raise DomainError(
                ErrorCode.QUOTA_EXCEEDED, "Restore cleanup inventory exceeds its byte budget."
            )
        changed = await self.database.restore_targets.update_one(
            ns
            | {
                "id": str(original.id),
                "cleanup_job_id": {"$exists": False},
                "write_tracking_version": 1,
            },
            {
                "$set": {"state": "abandoned", "cleanup_job_id": str(operation.job.id)},
                "$inc": {"write_epoch": 1},
            },
            session=session,
        )
        if not changed.matched_count:
            raise invalid()
        await self.database.restore_cleanup_targets.insert_one(
            cleanup.model_dump(mode="json"), session=session
        )
        return cleanup

    async def record(
        self, operation: OwnerOperation, session: AsyncClientSession | None = None
    ) -> RestoreCleanupTarget:
        if not isinstance(operation.command, RestoreCleanupCommand):
            raise inaccessible_reference()
        row = await self.database.restore_cleanup_targets.find_one(
            {
                "org_id": str(operation.org_id),
                "kg_id": str(operation.kg_id),
                "id": str(operation.id),
            },
            session=session,
        )
        if row is None:
            raise invalid()
        target = RestoreCleanupTarget.model_validate(
            {key: row[key] for key in RestoreCleanupTarget.model_fields}
        )
        if target.job != operation.job or target.restore_job.id != operation.command.restore_job_id:
            raise invalid()
        return target
