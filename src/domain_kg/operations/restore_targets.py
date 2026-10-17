"""Inactive restore reservations are not catalog entries or application grants."""

from typing import TYPE_CHECKING, Literal, Self
from uuid import UUID, uuid4

from pydantic import model_validator

from domain_kg.catalog.models import StorageProfile
from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import Action
from domain_kg.jobs.queue import now
from domain_kg.models import Namespace, ScopedRef
from domain_kg.operations.backup_models import BackupSeal

if TYPE_CHECKING:
    from pymongo.asynchronous.client_session import AsyncClientSession

    from domain_kg.identity.models import AccessContext
    from domain_kg.jobs.models import LeasedJob
    from domain_kg.jobs.queue import JobQueue
    from domain_kg.operations.models import OwnerOperation


class RestoreTarget(ScopedRef):
    job: ScopedRef
    target: Namespace
    generation: UUID
    placement: StorageProfile
    state: Literal["allocated", "preparing", "verified", "activated", "abandoned"] = "allocated"

    @model_validator(mode="after")
    def inactive_namespace(self) -> Self:
        if (
            (self.job.org_id, self.job.kg_id) != (self.org_id, self.kg_id)
            or self.target.org_id != self.org_id
            or self.target.kg_id == self.kg_id
            or self.org_id not in self.placement.allowed_org_ids
        ):
            raise ValueError(
                "restore target must be a distinct approved namespace in this organization"
            )
        return self


class RestoreTargets:
    def __init__(self, queue: "JobQueue") -> None:
        self.queue, self.database = queue, queue.database

    async def initialize(self) -> None:
        await self.database.restore_targets.create_index(
            [("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True
        )
        await self.database.restore_targets.create_index(
            [("target.org_id", 1), ("target.kg_id", 1)], unique=True
        )

    async def allocate(
        self, operation: "OwnerOperation", session: "AsyncClientSession"
    ) -> RestoreTarget:
        from domain_kg.operations.models import RestoreCommand

        command = operation.command
        if not isinstance(command, RestoreCommand) or operation.actor.kind != "human":
            raise inaccessible_reference()
        profile = await self.database.storage_profiles.find_one(
            {"id": str(command.placement_id), "allowed_org_ids": str(operation.org_id)},
            session=session,
        )
        if profile is None:
            raise inaccessible_reference()
        # The source job and seal are immutable inputs, not a client-chosen path.
        await self._backup(operation, session)
        target = RestoreTarget(
            org_id=operation.org_id,
            kg_id=operation.kg_id,
            id=operation.id,
            job=operation.job,
            target=Namespace(org_id=operation.org_id, kg_id=uuid4()),
            generation=uuid4(),
            placement=StorageProfile.model_validate(
                {k: v for k, v in profile.items() if k != "_id"}
            ),
        )
        await self.database.restore_targets.insert_one(
            target.model_dump(mode="json"), session=session
        )
        # Profile detach and reservation serialize on the existing registry row.
        await self.database.registry_state.update_one(
            {"_id": "placement"}, {"$inc": {"revision": 1}}, upsert=True, session=session
        )
        return target

    async def _backup(
        self, operation: "OwnerOperation", session: "AsyncClientSession | None" = None
    ) -> "BackupSeal":
        from domain_kg.operations.jobs import OwnerOperations
        from domain_kg.operations.models import RestoreCommand

        command = operation.command
        if not isinstance(command, RestoreCommand):
            raise inaccessible_reference()
        ns = {"org_id": str(operation.org_id), "kg_id": str(operation.kg_id)}
        job = await self.database.jobs.find_one(
            ns | {"id": str(command.backup_job_id), "kind": "backup", "stage": "ready"},
            session=session,
        )
        if job is None:
            raise DomainError(
                ErrorCode.REVISION_NOT_READY, "A completed scoped backup is required."
            )
        access = await self.queue.policy.resolve_access(
            operation.actor,
            [operation.kg_id],
            Action.RESTORE,
            org_id=operation.org_id,
            session=session,
        )
        backup = await OwnerOperations(self.queue).authorize(access, job, session)
        row = await self.database.backup_results.find_one(
            ns | {"id": str(backup.id), "state": "sealed"}, session=session
        )
        try:
            if row is None:
                raise ValueError("missing seal")
            seal = BackupSeal.model_validate(
                {field: row[field] for field in BackupSeal.model_fields}
            )
            if (
                seal.manifest.org_id,
                seal.manifest.kg_id,
                seal.manifest.backup_id,
                seal.manifest.attempt_id,
                seal.job_id,
                seal.snapshot,
                seal.manifest.sha256,
            ) != (
                operation.org_id,
                operation.kg_id,
                backup.id,
                UUID(job["attempt_id"]),
                command.backup_job_id,
                backup.snapshot,
                job.get("accepted_operation_hash"),
            ) or (job.get("accepted_operation_id") != str(backup.id)):
                raise ValueError("backup ownership differs")
        except (ValueError, KeyError, TypeError):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Restore backup binding is invalid."
            ) from None
        if seal.expires_at <= now():
            raise inaccessible_reference()
        return seal

    async def record(
        self, operation: "OwnerOperation", session: "AsyncClientSession | None" = None
    ) -> RestoreTarget:
        from domain_kg.operations.models import RestoreCommand

        row = await self.database.restore_targets.find_one(
            {
                "org_id": str(operation.org_id),
                "kg_id": str(operation.kg_id),
                "id": str(operation.id),
            },
            session=session,
        )
        try:
            if row is None or not isinstance(operation.command, RestoreCommand):
                raise ValueError("missing restore target")
            target = RestoreTarget.model_validate({k: v for k, v in row.items() if k != "_id"})
            if target.job != operation.job or target.placement.id != operation.command.placement_id:
                raise ValueError("target job or placement differs")
        except (ValueError, KeyError, TypeError):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Restore target binding is invalid."
            ) from None
        return target

    async def get(self, access: "AccessContext", kg: UUID, job_id: UUID) -> RestoreTarget:
        from domain_kg.operations.jobs import OwnerOperations

        current = await self.queue.policy.revalidate(access, action=Action.RESTORE)
        if kg not in current.kg_ids:
            raise inaccessible_reference()
        raw = await self.database.jobs.find_one(
            {"org_id": str(current.org_id), "kg_id": str(kg), "id": str(job_id), "kind": "restore"}
        )
        if raw is None:
            raise inaccessible_reference()
        operation = await OwnerOperations(self.queue).authorize(current, raw)
        target = await self.record(operation)
        await self.queue.policy.revalidate(current, action=Action.RESTORE)
        return target

    async def guard(self, job: "LeasedJob") -> RestoreTarget:
        from domain_kg.operations.capture import BackupCapture
        from domain_kg.operations.jobs import OwnerOperations

        if job.kind != "restore":
            raise inaccessible_reference()
        access = await self.queue.authorize(job)
        operation = await OwnerOperations(self.queue).authorize(
            access, await self.queue._current(job)
        )
        target = await self.record(operation)
        if target.state not in ("allocated", "preparing", "verified"):
            raise inaccessible_reference()
        if await self.database.kgs.find_one(
            {"org_id": str(target.target.org_id), "id": str(target.target.kg_id)}
        ):
            raise DomainError(
                ErrorCode.VERSION_CONFLICT, "Restore requires a fresh inactive target."
            )
        current = await self.queue.router.registry.profile(target.placement.id, job.org_id)
        if current != target.placement:
            raise DomainError(ErrorCode.VERSION_CONFLICT, "Approved restore placement changed.")
        await self._backup(operation)
        await BackupCapture(self.queue)._boundary(job, operation.snapshot)
        await self.queue.policy.revalidate(access, action=Action.RESTORE)
        return target
