"""Only leased, current-owner jobs can erase their preclaimed expired package objects."""

from functools import partial

from pydantic import ValidationError
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import inaccessible_reference
from domain_kg.identity.models import AccessContext
from domain_kg.jobs.models import LeasedJob
from domain_kg.jobs.queue import JobQueue, now
from domain_kg.materials.service import digest
from domain_kg.operations.audit import AuditTrail
from domain_kg.operations.export_models import ExportStore
from domain_kg.operations.models import OwnerOperation
from domain_kg.operations.package_erasure import PackageErasure
from domain_kg.operations.packages import PackageObject
from domain_kg.operations.retention_inventory import RetentionInventory
from domain_kg.operations.retention_results import RetentionResults
from domain_kg.operations.retention_targets import (
    RetainedTarget,
    RetentionPlan,
    RetentionTargets,
    invalid,
)
from domain_kg.operations.storage import PackageStore


class RetentionService:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database = queue, queue.database

    async def _operation(
        self, job: LeasedJob, session: AsyncClientSession | None = None
    ) -> tuple[AccessContext, OwnerOperation]:
        access = await self.queue.authorize(job, session)
        raw = await self.database.owner_operations.find_one(
            {"org_id": str(job.org_id), "kg_id": str(job.kg_id), "id": str(job.target_id)},
            session=session,
        )
        if raw is None:
            raise invalid()
        return access, OwnerOperation.model_validate(
            {key: raw[key] for key in OwnerOperation.model_fields}
        )

    async def _guard(
        self,
        job: LeasedJob,
        plan: RetentionPlan,
        target: RetainedTarget | None = None,
        session: AsyncClientSession | None = None,
    ) -> AccessContext:
        access, operation = await self._operation(job, session)
        if await RetentionTargets(self.queue).record(operation, session) != plan:
            raise invalid()
        kg = await self.database.kgs.find_one(
            {"org_id": str(job.org_id), "id": str(job.kg_id)}, session=session
        )
        if kg is None or (kg.get("storage_profile_id"), kg.get("configuration_epoch", 0)) != (
            str(plan.storage_profile_id),
            operation.snapshot.configuration_epoch,
        ):
            raise invalid()
        if target is not None:
            await RetentionTargets(self.queue).validate(operation, target, session)
            claim = await self.database.retention_claims.find_one(
                {
                    "org_id": str(job.org_id),
                    "kg_id": str(job.kg_id),
                    "kind": target.kind,
                    "operation_id": str(target.operation_id),
                    "attempt_id": str(target.attempt_id),
                    "cleanup_job_id": str(job.id),
                },
                session=session,
            )
            if claim is None:
                raise invalid()
        return access

    async def _erase(
        self,
        job: LeasedJob,
        plan: RetentionPlan,
        target: RetainedTarget,
        inventory: RetentionInventory,
        store: PackageStore,
    ) -> None:
        async def guard() -> None:
            await self._guard(job, plan, target)

        erasure = PackageErasure(store, authorize=guard, limits=plan.policy.erasure)
        await inventory.verify()
        async for row in (
            self.database.retention_objects.find(inventory.key).sort("position", 1).limit(100001)
        ):
            await erasure.erase(PackageObject.model_validate(row["reference"]))
        await guard()
        await self.database.retention_inventories.update_one(
            inventory.key | {"sealed": True}, {"$set": {"erased": True, "erased_at": now()}}
        )

    async def run(self, job: LeasedJob) -> None:
        if job.kind != "retention":
            raise inaccessible_reference()
        if await RetentionResults(self.queue).accepted(job):
            return
        try:
            access, operation = await self._operation(job)
            plan = await RetentionTargets(self.queue).record(operation)
            for target in plan.targets:
                access = await self._guard(job, plan, target)
                binding = (await self.queue.router.for_sources(access, job.kg_id)).blobs
                store = (PackageStore if target.kind == "backup" else ExportStore)(binding)
                inventory = RetentionInventory(self.queue, plan, target)
                await inventory.prepare(store, partial(self._guard, job, plan, target))
                await self._erase(job, plan, target, inventory, store)
            await self._complete(job, plan)
        except (ValidationError, KeyError, ValueError, TypeError):
            raise invalid() from None

    async def _complete(self, job: LeasedJob, plan: RetentionPlan) -> None:
        async def commit(session: AsyncClientSession) -> None:
            access = await self._guard(job, plan, session=session)
            await self.queue.transactions.fence(access, access.action, session)
            for target in plan.targets:
                await self._guard(job, plan, target, session)
                inventory = RetentionInventory(self.queue, plan, target)
                if not await self.database.retention_inventories.find_one(
                    inventory.key | {"sealed": True, "erased": True}, session=session
                ):
                    raise invalid()
            checksum = digest(plan.model_dump(mode="json"))
            await self.database.retention_results.insert_one(
                {
                    "org_id": str(job.org_id),
                    "kg_id": str(job.kg_id),
                    "id": str(job.target_id),
                    "job_id": str(job.id),
                    "attempt_id": str(job.attempt_id),
                    "state": "completed",
                    "plan_hash": checksum,
                    "target_count": len(plan.targets),
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
                access, job.kg_id, job.target_id, job.id, "retention", "completed", session
            )

        await self.queue.transactions.transaction(commit)
