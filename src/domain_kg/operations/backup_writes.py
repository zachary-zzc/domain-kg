"""Durable write intents distinguish acknowledged failed captures from uncertain I/O."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar
from uuid import uuid5

from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import Action
from domain_kg.jobs.models import LeasedJob
from domain_kg.jobs.queue import JobQueue, now
from domain_kg.operations.capture import BackupCapture
from domain_kg.operations.packages import PackageObject

T = TypeVar("T")


async def drain(task: asyncio.Task[Any]) -> bool:
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    return cancelled


def attempt_key(job: LeasedJob) -> dict[str, str]:
    return {
        "org_id": str(job.org_id),
        "kg_id": str(job.kg_id),
        "id": str(uuid5(job.attempt_id, f"backup-capture/{job.target_id}")),
    }


class BackupWrites:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database = queue, queue.database

    async def stopped(self, job: LeasedJob) -> None:
        await self.database.backup_attempts.update_one(
            attempt_key(job) | {"state": {"$ne": "sealed"}},
            {"$set": {"state": "stopped", "stopped_at": now()}},
        )

    async def begin(self, job: LeasedJob, reference: PackageObject) -> dict[str, str]:
        if job.kind != "backup" or (
            reference.org_id,
            reference.kg_id,
            reference.backup_id,
            reference.attempt_id,
        ) != (job.org_id, job.kg_id, job.target_id, job.attempt_id):
            raise inaccessible_reference()
        await BackupCapture(self.queue).guard(job)
        for table in ("backup_attempts", "backup_writes"):
            await self.database[table].create_index(
                [("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True
            )
        key = {"org_id": str(job.org_id), "kg_id": str(job.kg_id), "id": str(reference.id)}

        async def admit(session: AsyncClientSession) -> None:
            access = await self.queue.authorize(job, session)
            await self.queue.transactions.fence(access, Action.BACKUP, session)
            current = await self.queue._current(job, session)
            created = now()
            await self.database.backup_attempts.update_one(
                attempt_key(job),
                {
                    "$setOnInsert": {
                        **attempt_key(job),
                        "job_id": str(job.id),
                        "operation_id": str(job.target_id),
                        "attempt_id": str(job.attempt_id),
                        "tracking_version": 1,
                        "write_epoch": 0,
                        "pending_count": 0,
                        "uncertain_count": 0,
                        "state": "capturing",
                        "created_at": created,
                        "original_fence": current["fence"],
                    }
                },
                upsert=True,
                session=session,
            )
            changed = await self.database.backup_attempts.update_one(
                attempt_key(job) | {"cleanup_job_id": {"$exists": False}, "tracking_version": 1},
                {"$inc": {"write_epoch": 1, "pending_count": 1}},
                session=session,
            )
            if not changed.matched_count:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "Backup attempt is being collected.")
            await self.database.backup_writes.insert_one(
                key
                | {
                    "job_id": str(job.id),
                    "operation_id": str(job.target_id),
                    "attempt_id": str(job.attempt_id),
                    "attempt_key": attempt_key(job)["id"],
                    "reference": reference.model_dump(mode="json"),
                    "state": "pending",
                    "created_at": created,
                },
                session=session,
            )

        await self.queue.transactions.transaction(admit)
        return key

    async def finish(self, job: LeasedJob, key: dict[str, str], *, uncertain: bool) -> None:
        async def settle(session: AsyncClientSession) -> None:
            finished = now()
            changed = await self.database.backup_writes.update_one(
                key | {"state": "pending"},
                {
                    "$set": {
                        "state": "uncertain" if uncertain else "settled",
                        "finished_at": finished,
                    }
                },
                session=session,
            )
            if not changed.matched_count:
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Backup write settlement differs."
                )
            await self.database.backup_attempts.update_one(
                attempt_key(job),
                {
                    "$inc": {
                        "write_epoch": 1,
                        "pending_count": -1,
                        "uncertain_count": int(uncertain),
                    },
                    "$set": {"last_settled_at": finished},
                },
                session=session,
            )

        await self.queue.transactions.transaction(settle)

    async def run(
        self, job: LeasedJob, reference: PackageObject, action: Callable[[], Awaitable[T]]
    ) -> T:
        key = await self.begin(job, reference)

        async def execute() -> T:
            return await action()

        task = asyncio.create_task(execute())
        cancelled = await drain(task)
        error = None if task.cancelled() else task.exception()
        local = isinstance(error, DomainError) and error.code in (
            ErrorCode.VERSION_CONFLICT,
            ErrorCode.POLICY_CHANGED,
            ErrorCode.RESOURCE_NOT_ACCESSIBLE,
            ErrorCode.FORBIDDEN,
        )
        uncertain = task.cancelled() or (error is not None and not local)
        settlement = asyncio.create_task(self.finish(job, key, uncertain=uncertain))
        cancelled = await drain(settlement) or cancelled
        settlement.result()
        if cancelled:
            if not task.cancelled():
                task.exception()
            raise asyncio.CancelledError
        return task.result()
