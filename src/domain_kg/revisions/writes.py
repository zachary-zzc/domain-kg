"""Track and drain derived-store writes before permitting generation collection."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar
from uuid import UUID, uuid4

from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.jobs.models import LeasedJob
from domain_kg.jobs.queue import JobQueue, now
from domain_kg.revisions.projections import digest

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


class ProjectionWrites:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database = queue, queue.database

    async def begin(
        self, job: LeasedJob, generation: UUID, manifest_id: UUID | None
    ) -> dict[str, str]:
        if job.kind not in (
            "ingest",
            "replace",
            "import",
            "repair",
            "restore_material",
            "reprocess_material",
            "curate",
            "maintenance",
            "generation",
        ):
            raise inaccessible_reference()
        for table in ("projection_generations", "projection_writes"):
            await self.database[table].create_index(
                [("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True
            )
        ns = {"org_id": str(job.org_id), "kg_id": str(job.kg_id)}
        generation_key = ns | {"id": str(generation)}
        key = ns | {"id": str(uuid4())}

        async def admit(session: AsyncClientSession) -> None:
            access = await self.queue.authorize(job, session)
            await self.queue.transactions.fence(access, access.action, session)
            kg = await self.database.kgs.find_one(
                {"org_id": ns["org_id"], "id": ns["kg_id"]}, session=session
            )
            if kg is None:
                raise inaccessible_reference()
            if job.kind == "generation":
                from domain_kg.operations.generation_capture import GenerationCapture

                _, _, target = await GenerationCapture(self.queue).guard(job, session)
                if target.generation != generation:
                    raise inaccessible_reference()
            elif kg["generation"] != str(generation):
                raise DomainError(ErrorCode.VERSION_CONFLICT, "Projection generation changed.")
            profile = await self.queue.router.registry.profile(
                UUID(kg["storage_profile_id"]), job.org_id
            )
            profile_hash = digest(profile.model_dump(mode="json"))
            if manifest_id is not None:
                attempt = await self.database.projection_attempts.find_one(
                    ns | {"id": str(manifest_id), "batch.generation": str(generation)},
                    session=session,
                )
                if attempt is None or attempt.get("job") != job.model_dump(mode="json"):
                    raise inaccessible_reference()
            await self.database.projection_generations.update_one(
                generation_key,
                {
                    "$setOnInsert": {
                        **generation_key,
                        "tracking_version": 1,
                        "write_epoch": 0,
                        "pending_count": 0,
                        "uncertain_count": 0,
                        "storage_profile_id": kg["storage_profile_id"],
                        "storage_profile_hash": profile_hash,
                        "created_at": now(),
                    }
                },
                upsert=True,
                session=session,
            )
            changed = await self.database.projection_generations.update_one(
                generation_key
                | {
                    "tracking_version": 1,
                    "cleanup_job_id": {"$exists": False},
                    "storage_profile_id": kg["storage_profile_id"],
                    "storage_profile_hash": profile_hash,
                },
                {"$inc": {"write_epoch": 1, "pending_count": 1}},
                session=session,
            )
            if not changed.matched_count:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "Generation is being collected.")
            await self.database.projection_writes.insert_one(
                key
                | {
                    "generation": str(generation),
                    "manifest_id": str(manifest_id) if manifest_id else None,
                    "job_id": str(job.id),
                    "attempt_id": str(job.attempt_id),
                    "fence": job.fence,
                    "state": "pending",
                    "created_at": now(),
                },
                session=session,
            )
            # Serialize write admission against cancellation, retries and a generation switch.
            changed = await self.database.jobs.update_one(
                self.queue._active(job), {"$inc": {"projection_write_epoch": 1}}, session=session
            )
            if not changed.matched_count:
                raise inaccessible_reference()
            await self.database.kgs.update_one(
                {"org_id": ns["org_id"], "id": ns["kg_id"]},
                {"$inc": {"projection_write_epoch": 1}},
                session=session,
            )

        await self.queue.transactions.transaction(admit)
        return key

    async def finish(self, key: dict[str, str], generation: UUID, *, uncertain: bool) -> None:
        async def settle(session: AsyncClientSession) -> None:
            finished = now()
            changed = await self.database.projection_writes.update_one(
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
                    ErrorCode.PROJECTION_INCONSISTENT, "Projection write settlement differs."
                )
            await self.database.projection_generations.update_one(
                {"org_id": key["org_id"], "kg_id": key["kg_id"], "id": str(generation)},
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
        self,
        job: LeasedJob,
        generation: UUID,
        manifest_id: UUID | None,
        action: Callable[[], Awaitable[T]],
    ) -> T:
        key = await self.begin(job, generation, manifest_id)

        async def execute() -> T:
            return await action()

        task = asyncio.create_task(execute())
        cancelled = await drain(task)
        # Every remote exception is conservative: even a timeout can hide a completed write.
        uncertain = task.cancelled() or task.exception() is not None
        settling = asyncio.create_task(self.finish(key, generation, uncertain=uncertain))
        cancelled = await drain(settling) or cancelled
        settling.result()
        if cancelled:
            if not task.cancelled():
                task.exception()
            raise asyncio.CancelledError
        return task.result()
