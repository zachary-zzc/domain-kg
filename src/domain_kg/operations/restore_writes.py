"""Durable target-write intents drain cancellation and preserve uncertain I/O."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Literal, TypeVar
from uuid import uuid4

from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.jobs.models import LeasedJob
from domain_kg.jobs.queue import JobQueue, now
from domain_kg.materials.models import BlobRef
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.restore_targets import RestoreTarget, RestoreTargets

WriteKind = Literal["provision", "blob", "graph", "vector"]
T = TypeVar("T")


async def drain(task: asyncio.Task[Any]) -> bool:
    """A caller can be cancelled repeatedly; its actual remote operation is shielded."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    return cancelled


class RestoreWrites:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database = queue, queue.database

    async def _begin(
        self, job: LeasedJob, target: RestoreTarget, kind: WriteKind, reference: BlobRef | None
    ) -> dict[str, str]:
        if job.kind != "restore" or kind not in ("provision", "blob", "graph", "vector"):
            raise inaccessible_reference()
        if (kind == "blob") != (reference is not None) or (
            reference is not None
            and (reference.org_id, reference.kg_id) != (target.target.org_id, target.target.kg_id)
        ):
            raise inaccessible_reference()
        if await RestoreTargets(self.queue).guard(job) != target:
            raise inaccessible_reference()
        await self.database.restore_writes.create_index(
            [("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True
        )
        await self.database.restore_writes.create_index(
            [("org_id", 1), ("kg_id", 1), ("job_id", 1), ("state", 1)]
        )
        key = {"org_id": str(job.org_id), "kg_id": str(job.kg_id), "id": str(uuid4())}

        async def admit(session: AsyncClientSession) -> None:
            access = await self.queue.authorize(job, session)
            await self.queue.transactions.fence(access, access.action, session)
            operation = await OwnerOperations(self.queue).authorize(
                access, await self.queue._current(job, session), session
            )
            current = await RestoreTargets(self.queue).record(operation, session)
            if current != target or current.state not in ("allocated", "preparing", "verified"):
                raise inaccessible_reference()
            if await self.database.kgs.find_one(
                {"org_id": str(target.target.org_id), "id": str(target.target.kg_id)},
                session=session,
            ):
                raise inaccessible_reference()
            changed = await self.database.restore_targets.update_one(
                {
                    "org_id": str(job.org_id),
                    "kg_id": str(job.kg_id),
                    "id": str(job.target_id),
                    "state": {"$in": ["allocated", "preparing", "verified"]},
                    "cleanup_job_id": {"$exists": False},
                    "write_tracking_version": 1,
                },
                {"$inc": {"write_epoch": 1}},
                session=session,
            )
            if not changed.matched_count:
                raise inaccessible_reference()
            await self.database.restore_writes.insert_one(
                key
                | {
                    "job_id": str(job.id),
                    "operation_id": str(job.target_id),
                    "attempt_id": str(job.attempt_id),
                    "fence": job.fence,
                    "target": target.target.model_dump(mode="json"),
                    "kind": kind,
                    "reference": reference.model_dump(mode="json") if reference else None,
                    "state": "pending",
                    "created_at": now(),
                },
                session=session,
            )

        await self.queue.transactions.transaction(admit)
        return key

    async def _finish(self, key: dict[str, str], state: Literal["settled", "uncertain"]) -> None:
        # Recording settlement cannot require the old owner's still-current grants:
        # cancellation/revocation must drain already-issued I/O, without authorizing more.
        changed = await self.database.restore_writes.update_one(
            key | {"state": "pending"}, {"$set": {"state": state, "finished_at": now()}}
        )
        if not changed.matched_count:
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Restore write settlement differs."
            )

    async def run(
        self,
        job: LeasedJob,
        target: RestoreTarget,
        kind: WriteKind,
        action: Callable[[], Awaitable[T]],
        *,
        reference: BlobRef | None = None,
    ) -> T:
        key = await self._begin(job, target, kind, reference)

        async def execute() -> T:
            return await action()

        task = asyncio.create_task(execute())
        cancelled = await drain(task)
        failed = task.cancelled() or task.exception() is not None
        finishing = asyncio.create_task(self._finish(key, "uncertain" if failed else "settled"))
        cancelled = await drain(finishing) or cancelled
        finishing.result()
        if cancelled:
            if not task.cancelled():
                task.exception()
            raise asyncio.CancelledError
        return task.result()
