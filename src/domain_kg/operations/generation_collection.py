"""Collect only retired or abandoned derived generations with acknowledged writers."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pymongo.asynchronous.client_session import AsyncClientSession
from qdrant_client import models

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import AccessContext
from domain_kg.jobs.models import LeasedJob
from domain_kg.jobs.queue import JobQueue, now
from domain_kg.models import SHA256, FrozenModel, Namespace, NonNegative, UTCDateTime
from domain_kg.operations.models import OwnerOperation
from domain_kg.operations.retention_policy import RetentionPolicy
from domain_kg.revisions.projections import digest


def invalid() -> DomainError:
    return DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Generation collection boundary differs.")


class GenerationCollectionTarget(FrozenModel):
    kind: Literal["generation"] = "generation"
    generation: UUID
    storage_profile_id: UUID
    storage_profile_hash: SHA256
    write_epoch: NonNegative
    writes_count: NonNegative
    writes_hash: SHA256
    manifests_hash: SHA256
    settled_at: UTCDateTime

    @property
    def operation_id(self) -> UUID:
        return self.generation


class GenerationCollection:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database = queue, queue.database

    async def inventory(
        self,
        operation: OwnerOperation,
        generation: UUID,
        session: AsyncClientSession | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        ns = {"org_id": str(operation.org_id), "kg_id": str(operation.kg_id)}
        writes = (
            await self.database.projection_writes.find(
                ns | {"generation": str(generation)}, session=session
            )
            .sort("id", 1)
            .limit(10001)
            .to_list()
        )
        attempts = (
            await self.database.projection_attempts.find(
                ns | {"batch.generation": str(generation)}, session=session
            )
            .sort("id", 1)
            .limit(1001)
            .to_list()
        )
        if (
            not writes
            or len(writes) > 10000
            or len(attempts) > 1000
            or any(
                type(row.get("record_count")) is not int or row["record_count"] < 0
                for row in attempts
            )
            or sum(row["record_count"] for row in attempts) > 100000
        ):
            raise invalid()
        manifests = {row["id"] for row in attempts}
        jobs: dict[str, dict[str, Any] | None] = {}
        for row in writes:
            if row.get("state") != "settled" or (
                row.get("manifest_id") is not None and row["manifest_id"] not in manifests
            ):
                raise invalid()
            if row["job_id"] not in jobs:
                jobs[row["job_id"]] = await self.database.jobs.find_one(
                    ns | {"id": row["job_id"]}, session=session
                )
            job = jobs[row["job_id"]]
            if job is None or not (
                (
                    job.get("attempt_id") == row["attempt_id"]
                    and job.get("stage") in ("ready", "failed", "cancelled")
                )
                or (
                    job.get("attempt_id") != row["attempt_id"]
                    and job.get("fence", -1) > row["fence"]
                )
            ):
                raise invalid()
        for row in attempts:
            batch, original = row["batch"], row["job"]
            recorded_job = LeasedJob.model_validate(original)
            if row.get("write_tracking_version") != 1 or (
                batch["org_id"],
                batch["kg_id"],
                batch["manifest_id"],
                batch["generation"],
            ) != (ns["org_id"], ns["kg_id"], row["id"], str(generation)):
                raise invalid()
            if (recorded_job.org_id, recorded_job.kg_id) != (operation.org_id, operation.kg_id):
                raise invalid()
            if not any(
                value["manifest_id"] == row["id"]
                and value["job_id"] == original["id"]
                and value["attempt_id"] == original["attempt_id"]
                and value["fence"] == original["fence"]
                for value in writes
            ):
                raise invalid()
        return (
            [
                {
                    k: value.isoformat() if isinstance(value, datetime) else value
                    for k, value in row.items()
                    if k != "_id"
                }
                for row in writes
            ],
            [
                {
                    k: row.get(k)
                    for k in (
                        "id",
                        "batch",
                        "record_count",
                        "record_checksum",
                        "job",
                        "write_tracking_version",
                    )
                }
                for row in attempts
            ],
        )

    async def validate(
        self,
        operation: OwnerOperation,
        target: GenerationCollectionTarget,
        session: AsyncClientSession | None = None,
    ) -> None:
        ns = {"org_id": str(operation.org_id), "kg_id": str(operation.kg_id)}
        kg = await self.database.kgs.find_one(
            {"org_id": ns["org_id"], "id": ns["kg_id"]}, session=session
        )
        row = await self.database.projection_generations.find_one(
            ns
            | {
                "id": str(target.generation),
                "tracking_version": 1,
                "storage_profile_id": str(target.storage_profile_id),
                "storage_profile_hash": target.storage_profile_hash,
                "write_epoch": target.write_epoch,
                "pending_count": 0,
                "uncertain_count": 0,
            },
            session=session,
        )
        if (
            kg is None
            or row is None
            or (
                kg["generation"] == str(target.generation)
                or kg["storage_profile_id"] != str(target.storage_profile_id)
                or row.get("cleanup_job_id") not in (None, str(operation.job.id))
                or row.get("cleanup_state") == "erased"
                or max(
                    row["last_settled_at"], row.get("retired_at", row["last_settled_at"])
                ).replace(tzinfo=UTC)
                != target.settled_at
            )
        ):
            raise invalid()
        profile = await self.queue.router.registry.profile(
            target.storage_profile_id, operation.org_id
        )
        if digest(profile.model_dump(mode="json")) != target.storage_profile_hash:
            raise invalid()
        if await self.database.read_pins.find_one(
            ns
            | {
                "snapshot.generation": str(target.generation),
                "expires_at": {"$gt": now()},
            },
            session=session,
        ):
            raise DomainError(
                ErrorCode.REVISION_NOT_READY, "A retained read still pins the generation."
            )
        writes, manifests = await self.inventory(operation, target.generation, session)
        if (len(writes), digest(writes), digest(manifests)) != (
            target.writes_count,
            target.writes_hash,
            target.manifests_hash,
        ):
            raise invalid()

    async def allocate(
        self,
        operation: OwnerOperation,
        policy: RetentionPolicy,
        created: datetime,
        session: AsyncClientSession,
    ) -> tuple[GenerationCollectionTarget, ...]:
        ns = {"org_id": str(operation.org_id), "kg_id": str(operation.kg_id)}
        kg = await self.database.kgs.find_one(
            {"org_id": ns["org_id"], "id": ns["kg_id"]}, session=session
        )
        if kg is None:
            raise invalid()
        rows = (
            self.database.projection_generations.find(
                ns
                | {
                    "id": {"$ne": kg["generation"]},
                    "tracking_version": 1,
                    "storage_profile_id": kg["storage_profile_id"],
                    "pending_count": 0,
                    "uncertain_count": 0,
                    "last_settled_at": {"$lte": policy.cutoff(created)},
                    "cleanup_job_id": {"$exists": False},
                },
                session=session,
            )
            .sort([("last_settled_at", 1), ("id", 1)])
            .limit(100)
        )
        targets = []
        async for row in rows:
            settled = max(
                row["last_settled_at"], row.get("retired_at", row["last_settled_at"])
            ).replace(tzinfo=UTC)
            if settled > policy.cutoff(created):
                continue
            try:
                writes, manifests = await self.inventory(operation, UUID(row["id"]), session)
                if (
                    sum(value["record_count"] for value in manifests)
                    > policy.max_objects_per_target
                ):
                    continue
                target = GenerationCollectionTarget(
                    generation=row["id"],
                    storage_profile_id=row["storage_profile_id"],
                    storage_profile_hash=row["storage_profile_hash"],
                    write_epoch=row["write_epoch"],
                    writes_count=len(writes),
                    writes_hash=digest(writes),
                    manifests_hash=digest(manifests),
                    settled_at=settled,
                )
                await self.validate(operation, target, session)
            except DomainError:
                # Pending readers, active writers and pre-tracking generations stay protected.
                continue
            changed = await self.database.projection_generations.update_one(
                ns
                | {
                    "id": row["id"],
                    "write_epoch": row["write_epoch"],
                    "pending_count": 0,
                    "uncertain_count": 0,
                    "cleanup_job_id": {"$exists": False},
                },
                {"$set": {"cleanup_job_id": str(operation.job.id)}},
                session=session,
            )
            if not changed.matched_count:
                raise invalid()
            await self.database.kgs.update_one(
                {"org_id": ns["org_id"], "id": ns["kg_id"]},
                {"$inc": {"generation_collection_epoch": 1}},
                session=session,
            )
            await self.database.retention_claims.insert_one(
                ns
                | {
                    "kind": "generation",
                    "operation_id": row["id"],
                    "generation": row["id"],
                    "cleanup_job_id": str(operation.job.id),
                },
                session=session,
            )
            targets.append(target)
            if len(targets) == policy.max_targets:
                break
        return tuple(targets)

    async def erase(
        self,
        job: LeasedJob,
        target: GenerationCollectionTarget,
        authorize: Callable[[], Awaitable[AccessContext]],
    ) -> None:
        await authorize()
        profile = await self.queue.router.registry.profile(target.storage_profile_id, job.org_id)
        stores = await self.queue.router._bind_namespace(
            Namespace(org_id=job.org_id, kg_id=job.kg_id), profile, target.generation
        )
        # Fixed namespace filters; shared anchors and authoritative history remain intact.
        for _ in range(1001):
            await authorize()
            rows, _, _ = await stores.graph.driver.execute_query(
                "MATCH (n:ScopedProjection {org_id:$org,kg_id:$kg,generation:$gen}) "
                "WITH n LIMIT 100 DETACH DELETE n RETURN count(*) AS n",
                org=str(job.org_id),
                kg=str(job.kg_id),
                gen=str(target.generation),
            )
            if not rows or rows[0]["n"] == 0:
                break
        else:
            raise DomainError(
                ErrorCode.QUOTA_EXCEEDED, "Generation deletion exceeds its row budget."
            )
        await authorize()
        if await stores.vectors.client.collection_exists(stores.vectors.collection):
            query = models.Filter(
                must=[
                    models.FieldCondition(
                        key=key,
                        match=models.MatchValue(value=str(value)),
                    )
                    for key, value in {
                        "org_id": job.org_id,
                        "kg_id": job.kg_id,
                        "generation": target.generation,
                    }.items()
                ]
            )
            result = await stores.vectors.client.delete(
                stores.vectors.collection,
                points_selector=models.FilterSelector(filter=query),
                wait=True,
            )
            if result.status != models.UpdateStatus.COMPLETED:
                raise DomainError(
                    ErrorCode.REVISION_NOT_READY, "Generation deletion has not completed."
                )
            if (
                await stores.vectors.client.count(
                    stores.vectors.collection, count_filter=query, exact=True
                )
            ).count:
                raise invalid()
            await authorize()
            # A foreign/unexpected point never grants permission to drop a shared collection.
            if (
                await stores.vectors.client.count(stores.vectors.collection, exact=True)
            ).count == 0:
                await stores.vectors.client.delete_collection(stores.vectors.collection)
        await authorize()
        await self.database.projection_generations.update_one(
            {
                "org_id": str(job.org_id),
                "kg_id": str(job.kg_id),
                "id": str(target.generation),
                "cleanup_job_id": str(job.id),
                "write_epoch": target.write_epoch,
            },
            {"$set": {"deletion_verified": True}},
        )

    async def complete(
        self,
        operation: OwnerOperation,
        target: GenerationCollectionTarget,
        session: AsyncClientSession,
    ) -> None:
        changed = await self.database.projection_generations.update_one(
            {
                "org_id": str(operation.org_id),
                "kg_id": str(operation.kg_id),
                "id": str(target.generation),
                "cleanup_job_id": str(operation.job.id),
                "write_epoch": target.write_epoch,
                "deletion_verified": True,
            },
            {"$set": {"cleanup_state": "erased", "erased_at": now()}},
            session=session,
        )
        if not changed.matched_count:
            raise invalid()
