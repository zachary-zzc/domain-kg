"""Erase a permanently fenced inactive target, verify absence, then release quota."""

from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any, TypeVar

from pydantic import ValidationError
from pymongo.asynchronous.client_session import AsyncClientSession
from qdrant_client import models as qmodels

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext
from domain_kg.jobs.models import LeasedJob
from domain_kg.jobs.queue import JobQueue, now
from domain_kg.materials.service import digest
from domain_kg.operations.audit import AuditTrail
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.package_erasure import PackageErasure
from domain_kg.operations.restore_cleanup_results import RestoreCleanupResults
from domain_kg.operations.restore_cleanup_targets import (
    RestoreCleanupTarget,
    RestoreCleanupTargets,
    invalid,
)
from domain_kg.operations.restore_quota import RestoreQuota
from domain_kg.operations.restore_writes import drain
from domain_kg.operations.storage import PackageStore
from domain_kg.storage.router import RoutedStores

T = TypeVar("T")

CONTROL = (
    "material_access",
    "kg_schemas",
    "import_plans",
    "import_source_origins",
    "jobs",
    "job_artifacts",
    "job_checkpoints",
    "operation_artifacts",
    "curation_revisions",
    "curation_access",
    "curation_targets",
    "curation_scopes",
    "node_visibility",
    "projection_attempts",
    "projection_record_refs",
    "publication_manifests",
    "publication_bindings",
)
AUTHORITY = (
    "materials",
    "material_versions",
    "entities",
    "assertions",
    "supports",
    "curations",
    "version_blobs",
    "source_blocks",
    "source_chunks",
    "observations",
    "engine_names",
    "import_id_mappings",
    "import_assertion_mappings",
)


class RestoreCleanupService:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database = queue, queue.database

    async def guard(
        self,
        job: LeasedJob,
        target: RestoreCleanupTarget,
        session: AsyncClientSession | None = None,
    ) -> AccessContext:
        access = await self.queue.authorize(job, session)
        operation = await OwnerOperations(self.queue).authorize(
            access, await self.queue._current(job, session), session
        )
        if await RestoreCleanupTargets(self.queue).record(operation, session) != target:
            raise invalid()
        ns = {"org_id": str(job.org_id), "kg_id": str(job.kg_id)}
        original = await self.database.jobs.find_one(
            ns
            | {
                "id": str(target.restore_job.id),
                "kind": "restore",
                "target_id": str(target.restore_operation_id),
                "stage": {"$in": ["failed", "cancelled"]},
            },
            session=session,
        )
        recorded = await self.database.restore_targets.find_one(
            ns
            | {
                "id": str(target.restore_operation_id),
                "state": "abandoned",
                "cleanup_job_id": str(job.id),
                "write_tracking_version": 1,
            },
            session=session,
        )
        if (
            original is None
            or recorded is None
            or original.get("attempt_id")
            != (str(target.restore_attempt_id) if target.restore_attempt_id else None)
        ):
            raise invalid()
        if (recorded.get("target"), recorded.get("placement"), recorded.get("generation")) != (
            target.target.model_dump(mode="json"),
            target.placement.model_dump(mode="json"),
            str(target.generation),
        ):
            raise invalid()
        if await self.database.kgs.find_one(
            {"org_id": str(target.target.org_id), "id": str(target.target.kg_id)}, session=session
        ):
            raise invalid()
        if await self.database.restore_writes.find_one(
            ns | {"job_id": str(target.restore_job.id), "state": {"$ne": "settled"}},
            session=session,
        ):
            raise DomainError(
                ErrorCode.REVISION_NOT_READY, "Target write settlement is not established."
            )
        if (
            await self.queue.router.registry.profile(target.placement.id, job.org_id)
            != target.placement
        ):
            raise invalid()
        return access

    @staticmethod
    def vector_filter(target: RestoreCleanupTarget) -> qmodels.Filter:
        return qmodels.Filter(
            must=[
                qmodels.FieldCondition(key=key, match=qmodels.MatchValue(value=value))
                for key, value in target.target.model_dump(mode="json").items()
            ]
        )

    async def _objects(
        self, job: LeasedJob, target: RestoreCleanupTarget, stores: RoutedStores
    ) -> None:
        async def authorize() -> None:
            await self.guard(job, target)

        erasure = PackageErasure(
            PackageStore(stores.blobs), authorize=authorize, limits=target.policy.erasure
        )
        for reference in target.objects:
            await erasure.erase_original(reference)
        await erasure.verify_original_namespace_absent()

    async def _projections(
        self, job: LeasedJob, target: RestoreCleanupTarget, stores: RoutedStores
    ) -> None:
        import asyncio

        params: dict[str, Any] = {"org": str(target.target.org_id), "kg": str(target.target.kg_id)}
        await self.guard(job, target)
        rows, _, _ = await stores.graph.driver.execute_query(
            "MATCH (n {org_id:$org,kg_id:$kg}) RETURN count(n) AS total", **params
        )
        if rows[0]["total"] > target.policy.max_graph_nodes:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Target graph exceeds its cleanup bound.")
        foreign, _, _ = await stores.graph.driver.execute_query(
            "MATCH (n {org_id:$org,kg_id:$kg})-[r]-(m) "
            "WHERE m.org_id IS NULL OR m.kg_id IS NULL OR m.org_id<>$org OR m.kg_id<>$kg "
            "RETURN count(r) AS total",
            **params,
        )
        if foreign[0]["total"]:
            raise invalid()
        await self.guard(job, target)
        exists = await stores.vectors.client.collection_exists(stores.vectors.collection)
        query = self.vector_filter(target)
        if (
            exists
            and (
                await stores.vectors.client.count(
                    stores.vectors.collection, count_filter=query, exact=True
                )
            ).count
            > target.policy.max_vector_points
        ):
            raise DomainError(
                ErrorCode.QUOTA_EXCEEDED, "Target vectors exceed their cleanup bound."
            )

        # Drain destructive backend calls even if this cleanup worker is cancelled.
        async def settled(action: Callable[[], Awaitable[T]]) -> T:
            async def execute() -> T:
                return await action()

            task = asyncio.create_task(execute())
            cancelled = await drain(task)
            result = task.result()
            if cancelled:
                raise asyncio.CancelledError
            return result

        remaining = rows[0]["total"]
        while remaining:
            await self.guard(job, target)
            await settled(
                partial(
                    stores.graph.driver.execute_query,
                    "MATCH (n {org_id:$org,kg_id:$kg}) WITH n LIMIT $limit DETACH DELETE n",
                    **params,
                    limit=target.policy.batch_size,
                )
            )
            remaining -= min(remaining, target.policy.batch_size)
        if exists:
            await self.guard(job, target)
            await settled(
                partial(
                    stores.vectors.client.delete,
                    stores.vectors.collection,
                    points_selector=qmodels.FilterSelector(filter=query),
                    wait=True,
                )
            )
        await self._verify_projections(job, target, stores)

    async def _verify_projections(
        self, job: LeasedJob, target: RestoreCleanupTarget, stores: RoutedStores
    ) -> None:
        await self.guard(job, target)
        rows, _, _ = await stores.graph.driver.execute_query(
            "MATCH (n {org_id:$org,kg_id:$kg}) RETURN count(n) AS total",
            org=str(target.target.org_id),
            kg=str(target.target.kg_id),
        )
        if rows[0]["total"]:
            raise invalid()
        if await stores.vectors.client.collection_exists(stores.vectors.collection):
            if (
                await stores.vectors.client.count(
                    stores.vectors.collection, count_filter=self.vector_filter(target), exact=True
                )
            ).count:
                raise invalid()
        await self.guard(job, target)

    async def run(self, job: LeasedJob) -> None:
        if job.kind != "restore_cleanup":
            raise inaccessible_reference()
        if await RestoreCleanupResults(self.queue).accepted(job):
            return
        try:
            access = await self.queue.authorize(job)
            operation = await OwnerOperations(self.queue).authorize(
                access, await self.queue._current(job)
            )
            target = await RestoreCleanupTargets(self.queue).record(operation)
            await self.guard(job, target)
            stores = await self.queue.router._bind_namespace(
                target.target, target.placement, target.generation
            )
            await self._objects(job, target, stores)
            await self._projections(job, target, stores)
            await self._complete(job, target, stores)
        except (ValidationError, KeyError, TypeError, ValueError):
            raise invalid() from None

    async def _complete(
        self, job: LeasedJob, target: RestoreCleanupTarget, stores: RoutedStores
    ) -> None:
        async def commit(session: AsyncClientSession) -> None:
            access = await self.guard(job, target, session)
            await self.queue.transactions.fence(access, access.action, session)
            ns = target.target.model_dump(mode="json")
            # Authority/control deletes and quota settlement share one Mongo transaction.
            for database, tables in (
                (self.database, CONTROL),
                (stores.authority._database, AUTHORITY),
            ):
                for table in tables:
                    if (
                        await database[table].count_documents(
                            ns, limit=target.policy.max_rows_per_collection + 1, session=session
                        )
                        > target.policy.max_rows_per_collection
                    ):
                        raise DomainError(
                            ErrorCode.QUOTA_EXCEEDED, "Target metadata exceeds its cleanup bound."
                        )
                    await database[table].delete_many(ns, session=session)
            quota_key = {
                "org_id": str(job.org_id),
                "kg_id": str(job.kg_id),
                "id": str(target.restore_operation_id),
            }
            quota = await self.database.restore_quota_reservations.find_one(
                quota_key, session=session
            )
            if target.reservation is None:
                if quota is not None or target.objects:
                    raise invalid()
            else:
                if quota is None or RestoreQuota.saved(quota) != target.reservation:
                    raise invalid()
                await self.queue.transactions.quota(
                    access, target.target.kg_id, -target.reservation.byte_count, 0, session
                )
                changed = await self.database.restore_quota_reservations.update_one(
                    quota_key | {"state": "reserved"},
                    {"$set": {"state": "released", "released_at": now()}},
                    session=session,
                )
                if not changed.matched_count:
                    raise invalid()
            checksum = digest(target.model_dump(mode="json"))
            await self.database.restore_cleanup_results.insert_one(
                {
                    "org_id": str(job.org_id),
                    "kg_id": str(job.kg_id),
                    "id": str(job.target_id),
                    "job_id": str(job.id),
                    "attempt_id": str(job.attempt_id),
                    "state": "completed",
                    "target_hash": checksum,
                    "completed_at": now(),
                },
                session=session,
            )
            changed = await self.database.jobs.update_one(
                self.queue._active(job),
                {
                    "$set": {
                        "stage": "ready",
                        "progress": 1,
                        "lease_until": now(),
                        "error_code": None,
                        "accepted_operation_id": str(job.target_id),
                        "accepted_operation_hash": checksum,
                    }
                },
                session=session,
            )
            if not changed.matched_count:
                raise invalid()
            await AuditTrail(self.queue).append(
                access, job.kg_id, job.target_id, job.id, "restore_cleanup", "completed", session
            )

        await self.queue.transactions.transaction(commit)
