"""Reserve original storage before copying into an owned inactive restore target."""

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Self
from uuid import UUID

from pydantic import model_validator
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.models import LeasedJob
from domain_kg.jobs.queue import JobQueue, now
from domain_kg.models import SHA256, Namespace, NonNegative, ScopedRef, SourceVersion, UTCDateTime
from domain_kg.operations.backup_models import BackupManifest
from domain_kg.operations.capture import BackupCapture
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.packages import PackagePart
from domain_kg.operations.restore_targets import RestoreTarget, RestoreTargets

if TYPE_CHECKING:
    from domain_kg.operations.restore_preparation import PreparedRestore


def original_bytes(parts: tuple[PackagePart, ...], source: Namespace) -> int:
    seen: set[SourceVersion] = set()
    total = 0
    for part in parts:
        if (part.object.org_id, part.object.kg_id) != (source.org_id, source.kg_id):
            raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Restore quota source differs.")
        if part.kind != "original":
            continue
        if part.source is None or part.original is None or part.source in seen:
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Restore originals are duplicated."
            )
        seen.add(part.source)
        total += part.original.size_bytes
    return total


class RestoreQuotaReservation(ScopedRef):
    job_id: UUID
    target: Namespace
    source_header_sha: SHA256
    byte_count: NonNegative
    state: Literal["reserved", "committed", "released"] = "reserved"
    created_at: UTCDateTime
    accepted_at: UTCDateTime | None = None
    released_at: UTCDateTime | None = None

    @model_validator(mode="after")
    def fresh_target(self) -> Self:
        if self.target.org_id != self.org_id or self.target.kg_id == self.kg_id:
            raise ValueError("restore storage belongs to its distinct same-organization target")
        return self


class RestoreQuota:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database = queue, queue.database
        self.targets = RestoreTargets(queue)

    @staticmethod
    def invalid() -> DomainError:
        return DomainError(
            ErrorCode.PROJECTION_INCONSISTENT, "Restore storage reservation differs."
        )

    @staticmethod
    def saved(row: dict[str, Any]) -> RestoreQuotaReservation:
        value = {name: item for name, item in row.items() if name != "_id"}
        for field in ("created_at", "accepted_at", "released_at"):
            timestamp = value.get(field)
            if isinstance(timestamp, datetime) and timestamp.tzinfo is None:
                value[field] = timestamp.replace(tzinfo=UTC)
        return RestoreQuotaReservation.model_validate(value)

    def expected(
        self,
        job: LeasedJob,
        target: RestoreTarget,
        manifest: BackupManifest,
        parts: tuple[PackagePart, ...],
    ) -> RestoreQuotaReservation:
        if (
            manifest.org_id,
            manifest.kg_id,
            target.org_id,
            target.kg_id,
            target.id,
            target.job.id,
        ) != (job.org_id, job.kg_id, job.org_id, job.kg_id, job.target_id, job.id):
            raise self.invalid()

        if target.state not in ("allocated", "preparing", "verified") or any(
            (part.object.backup_id, part.object.attempt_id)
            != (manifest.backup_id, manifest.attempt_id)
            for part in parts
        ):
            raise self.invalid()
        return RestoreQuotaReservation(
            org_id=job.org_id,
            kg_id=job.kg_id,
            id=job.target_id,
            job_id=job.id,
            target=target.target,
            source_header_sha=hashlib.sha256(manifest.model_dump_json().encode()).hexdigest(),
            byte_count=original_bytes(parts, Namespace(org_id=job.org_id, kg_id=job.kg_id)),
            created_at=now(),
        )

    async def reserve(
        self,
        job: LeasedJob,
        target: RestoreTarget,
        manifest: BackupManifest,
        parts: tuple[PackagePart, ...],
    ) -> RestoreQuotaReservation:
        expected = self.expected(job, target, manifest, parts)
        await self.database.restore_quota_reservations.create_index(
            [("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True
        )
        key = {"org_id": str(job.org_id), "kg_id": str(job.kg_id), "id": str(job.target_id)}

        async def commit(session: AsyncClientSession) -> RestoreQuotaReservation:
            access = await self.queue.authorize(job, session)
            operation = await OwnerOperations(self.queue).authorize(
                access, await self.queue._current(job, session), session
            )
            current = await self.targets.record(operation, session)
            if current != target or await self.database.kgs.find_one(
                {"org_id": str(target.target.org_id), "id": str(target.target.kg_id)},
                session=session,
            ):
                raise self.invalid()
            seal = await self.targets._backup(operation, session)
            if seal.manifest.sha256 != expected.source_header_sha:
                raise self.invalid()
            await BackupCapture(self.queue)._boundary(job, operation.snapshot, session)
            await self.queue.transactions.fence(access, Action.RESTORE, session)
            saved = await self.database.restore_quota_reservations.find_one(key, session=session)
            if saved is not None:
                previous = self.saved(saved)
                if previous.model_dump(exclude={"created_at"}) != expected.model_dump(
                    exclude={"created_at"}
                ):
                    raise self.invalid()
                return previous
            # The real source RESTORE actor authorizes only this recorded fresh
            # target. No target access context or application grant is fabricated.
            await self.queue.transactions.quota(
                access, target.target.kg_id, expected.byte_count, 0, session
            )
            value = expected.model_dump(mode="json") | {"created_at": expected.created_at}
            await self.database.restore_quota_reservations.insert_one(value, session=session)
            return expected

        return await self.queue.transactions.transaction(commit)

    async def accept(
        self,
        job: LeasedJob,
        staged: "PreparedRestore",
        access: AccessContext,
        session: AsyncClientSession,
    ) -> None:
        current = await self.queue.policy.revalidate(access, action=Action.RESTORE, session=session)
        if current.org_id != job.org_id or job.kg_id not in current.kg_ids:
            raise self.invalid()
        expected = self.expected(
            job, staged.binding.target, staged.inventory.manifest, staged.inventory.parts
        )
        key = {"org_id": str(job.org_id), "kg_id": str(job.kg_id), "id": str(job.target_id)}
        raw = await self.database.restore_quota_reservations.find_one(key, session=session)
        if raw is None:
            raise self.invalid()
        saved = self.saved(raw)
        if saved.model_dump(exclude={"created_at"}) != expected.model_dump(exclude={"created_at"}):
            raise self.invalid()
        await self.queue.transactions.quota(
            current, saved.target.kg_id, -saved.byte_count, saved.byte_count, session
        )
        updated = await self.database.restore_quota_reservations.update_one(
            key | {"state": "reserved"},
            {"$set": {"state": "committed", "accepted_at": now()}},
            session=session,
        )
        if not updated.matched_count:
            raise self.invalid()
