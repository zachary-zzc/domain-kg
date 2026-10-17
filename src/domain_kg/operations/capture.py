"""Live owner capture pins and narrow reads of accepted, possibly fenced source artifacts."""

from datetime import UTC
from typing import Any, Literal, Self
from uuid import UUID, uuid5

from pydantic import ValidationError, model_validator
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.models import LeasedJob
from domain_kg.jobs.queue import JobQueue, now
from domain_kg.models import FrozenModel, NonNegative, ScopedRef, SourceVersion, UTCDateTime
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import OwnerOperation, SnapshotPointer
from domain_kg.revisions.journal import PublicationJournal
from domain_kg.revisions.models import PublishedView
from domain_kg.revisions.projections import AcceptedProjection


def pin_id(job: LeasedJob) -> UUID:
    return uuid5(job.id, f"backup-capture/{job.attempt_id}")


class CapturePin(ScopedRef):
    kind: Literal["backup_capture"] = "backup_capture"
    job_id: UUID
    operation_id: UUID
    attempt_id: UUID
    policy_revision: NonNegative
    snapshot: SnapshotPointer
    expires_at: UTCDateTime

    @model_validator(mode="after")
    def namespace(self) -> Self:
        if (self.snapshot.org_id, self.snapshot.kg_id) != (self.org_id, self.kg_id):
            raise ValueError("capture pins must match their snapshot namespace")
        return self


class BackupReadPermit(FrozenModel):
    job: LeasedJob
    source: SourceVersion
    pin_id: UUID

    @model_validator(mode="after")
    def exact_source(self) -> Self:
        if (
            self.job.kind != "backup"
            or self.pin_id != pin_id(self.job)
            or (self.source.org_id, self.source.kg_id) != (self.job.org_id, self.job.kg_id)
        ):
            raise ValueError("backup reads require their exact live owner capture")
        return self


class BackupCapture:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database = queue, queue.database
        self.operations = OwnerOperations(queue)

    async def _context(
        self, job: LeasedJob, session: AsyncClientSession | None = None
    ) -> tuple[AccessContext, OwnerOperation]:
        if job.kind != "backup":
            raise inaccessible_reference()
        access = await self.queue.authorize(job, session)
        raw = await self.queue._current(job, session)
        return access, await self.operations.authorize(access, raw, session)

    async def _boundary(
        self, job: LeasedJob, pointer: SnapshotPointer, session: AsyncClientSession | None = None
    ) -> None:
        row = await self.database.kgs.find_one(
            {"org_id": str(job.org_id), "id": str(job.kg_id)}, session=session
        )
        if row is None or (
            row.get("published_revision"),
            row.get("generation"),
            row.get("published_manifest_id"),
            row.get("visibility_revision"),
            row.get("revision", 0),
            row.get("configuration_epoch", 0),
        ) != (
            pointer.revision,
            str(pointer.generation),
            str(pointer.manifest_id) if pointer.manifest_id else None,
            pointer.visibility_revision,
            pointer.metadata_revision,
            pointer.configuration_epoch,
        ):
            raise DomainError(ErrorCode.VERSION_CONFLICT, "The backup capture boundary changed.")

    async def _pin(self, job: LeasedJob, session: AsyncClientSession | None = None) -> CapturePin:
        raw = await self.database.read_pins.find_one(
            {
                "org_id": str(job.org_id),
                "kg_id": str(job.kg_id),
                "id": str(pin_id(job)),
                "kind": "backup_capture",
                "job_id": str(job.id),
                "attempt_id": str(job.attempt_id),
                "operation_id": str(job.target_id),
                "expires_at": {"$gt": now()},
            },
            session=session,
        )
        if raw is None:
            raise DomainError(
                ErrorCode.REVISION_NOT_READY, "The live backup capture pin is missing."
            )
        try:
            values: dict[str, Any] = {key: value for key, value in raw.items() if key != "_id"}
            values["expires_at"] = values["expires_at"].replace(tzinfo=UTC)
            return CapturePin.model_validate(values)
        except (ValidationError, ValueError, KeyError, TypeError):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "The backup capture pin is invalid."
            ) from None

    async def guard(self, job: LeasedJob) -> AccessContext:
        access, operation = await self._context(job)
        pin = await self._pin(job)
        if pin.snapshot != operation.snapshot:
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Capture snapshot ownership differs."
            )
        if pin.policy_revision != access.policy_revision:
            raise DomainError(ErrorCode.POLICY_CHANGED, "Policy changed during backup capture.")
        await self._boundary(job, pin.snapshot)
        await self.queue.policy.revalidate(access)
        return access

    async def start(self, job: LeasedJob) -> PublishedView:
        access, operation = await self._context(job)
        view = await PublicationJournal(self.queue).load_metadata(access, job.kg_id)
        if (view.revision, view.generation, view.manifest_id) != (
            operation.snapshot.revision,
            operation.snapshot.generation,
            operation.snapshot.manifest_id,
        ):
            raise DomainError(
                ErrorCode.VERSION_CONFLICT, "The backup publication boundary changed."
            )
        await self.database.read_pins.create_index("expires_at", expireAfterSeconds=0)
        await self.database.read_pins.create_index(
            [("org_id", 1), ("kg_id", 1), ("id", 1)],
            unique=True,
            partialFilterExpression={"kind": "backup_capture"},
            name="backup_capture_identity",
        )

        async def protect(session: AsyncClientSession) -> None:
            current, record = await self._context(job, session)
            await self.queue.transactions.fence(current, current.action, session)
            await self._boundary(job, record.snapshot, session)
            raw_job = await self.queue._current(job, session)
            pin = CapturePin(
                org_id=job.org_id,
                kg_id=job.kg_id,
                id=pin_id(job),
                job_id=job.id,
                operation_id=job.target_id,
                attempt_id=job.attempt_id,
                policy_revision=current.policy_revision,
                snapshot=record.snapshot,
                expires_at=raw_job["lease_until"].replace(tzinfo=UTC),
            )
            key = {"org_id": str(job.org_id), "kg_id": str(job.kg_id), "id": str(pin.id)}
            await self.database.read_pins.update_one(
                key,
                {"$setOnInsert": pin.model_dump(mode="json") | {"expires_at": pin.expires_at}},
                upsert=True,
                session=session,
            )
            saved = await self._pin(job, session)
            if saved.snapshot != pin.snapshot or saved.policy_revision != current.policy_revision:
                raise DomainError(ErrorCode.POLICY_CHANGED, "The backup capture guard changed.")
            await self.database.kgs.update_one(
                {"org_id": str(job.org_id), "id": str(job.kg_id)},
                {"$inc": {"backup_capture_epoch": 1}},
                session=session,
            )

        await self.queue.transactions.transaction(protect)
        await self.guard(job)
        return view

    async def source_permit(self, job: LeasedJob, source: SourceVersion) -> BackupReadPermit:
        source = SourceVersion.model_validate(source)
        access = await self.guard(job)
        if (source.org_id, source.kg_id) != (job.org_id, job.kg_id):
            raise inaccessible_reference()
        pin = await self._pin(job)
        row = await self.database.publication_bindings.find_one(
            {
                "org_id": str(job.org_id),
                "kg_id": str(job.kg_id),
                "view_manifest_id": str(pin.snapshot.manifest_id),
                "owner_key": f"source:{source.material_id}",
            }
        )
        if row is None:
            raise inaccessible_reference()
        try:
            binding = AcceptedProjection.model_validate(row["binding"])
        except (ValidationError, KeyError, TypeError):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Backup source binding is invalid."
            ) from None
        if binding.source != source:
            raise inaccessible_reference()
        await self.queue.policy.revalidate(access)
        return BackupReadPermit(job=job, source=source, pin_id=pin.id)

    async def release(self, job: LeasedJob) -> None:
        # Internal cleanup deletes only this attempt, even after cancellation/revocation.
        await self.database.read_pins.delete_one(
            {
                "org_id": str(job.org_id),
                "kg_id": str(job.kg_id),
                "id": str(pin_id(job)),
                "kind": "backup_capture",
                "job_id": str(job.id),
                "attempt_id": str(job.attempt_id),
                "operation_id": str(job.target_id),
            }
        )


async def authorize_backup_read(
    queue: JobQueue, access: AccessContext, source: SourceVersion, permit: BackupReadPermit
) -> None:
    permit = BackupReadPermit.model_validate(permit)
    if (
        permit.source != source
        or access.org_id != source.org_id
        or source.kg_id not in access.kg_ids
        or access.action != Action.BACKUP
    ):
        raise inaccessible_reference()
    current = await BackupCapture(queue).guard(permit.job)
    if current.principal != access.principal:
        raise inaccessible_reference()
    await BackupCapture(queue).source_permit(permit.job, source)
