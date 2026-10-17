"""Persist a bounded, verified object inventory before the first destructive request."""

import hashlib
from collections.abc import Awaitable, Callable
from typing import Any

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.jobs.queue import JobQueue
from domain_kg.operations.backup_models import BackupManifest, BackupSeal
from domain_kg.operations.package_reader import PackageReader
from domain_kg.operations.packages import PackageObject
from domain_kg.operations.retention_targets import RetainedTarget, RetentionPlan, invalid
from domain_kg.operations.storage import PackageStore


class RetentionInventory:
    def __init__(self, queue: JobQueue, plan: RetentionPlan, target: RetainedTarget) -> None:
        self.database, self.plan, self.target = queue.database, plan, target
        self.key = {
            "org_id": str(plan.org_id),
            "kg_id": str(plan.kg_id),
            "cleanup_job_id": str(plan.job.id),
            "kind": target.kind,
            "operation_id": str(target.operation_id),
            "attempt_id": str(target.attempt_id),
        }

    async def initialize(self) -> None:
        keys = [(key, 1) for key in self.key]
        await self.database.retention_inventories.create_index(keys, unique=True)
        await self.database.retention_objects.create_index(keys + [("position", 1)], unique=True)
        await self.database.retention_objects.create_index(
            keys + [("reference.id", 1)], unique=True
        )

    async def prepare(self, store: PackageStore, guard: Callable[[], Awaitable[object]]) -> None:
        await guard()
        await self.initialize()
        if await self.database.retention_inventories.find_one(self.key | {"sealed": True}):
            await self.verify()
            return
        reader = PackageReader(store, guard)
        descriptor = self.target.descriptor
        position, total_bytes, checksum = 0, 0, hashlib.sha256()
        batch: list[PackageObject] = []

        async def save() -> None:
            nonlocal position, total_bytes
            for ref in batch:
                position += 1
                total_bytes += ref.size_bytes
                if (
                    position > self.plan.policy.max_objects_per_target
                    or total_bytes > self.plan.policy.max_verify_bytes
                ):
                    raise DomainError(
                        ErrorCode.QUOTA_EXCEEDED, "Cleanup inventory exceeds its bound."
                    )
                self._validate(ref)
                value = ref.model_dump(mode="json")
                checksum.update(ref.model_dump_json().encode() + b"\n")
                key = self.key | {"position": position}
                await guard()
                await self.database.retention_objects.update_one(
                    key, {"$setOnInsert": key | {"reference": value}}, upsert=True
                )
                row = await self.database.retention_objects.find_one(key)
                if row is None or row.get("reference") != value:
                    raise invalid()
            batch.clear()

        if isinstance(descriptor, BackupSeal):
            manifest = BackupManifest.model_validate_json(await reader.body(descriptor.manifest))
            if (
                manifest.org_id,
                manifest.kg_id,
                manifest.backup_id,
                manifest.attempt_id,
                manifest.job_id,
                manifest.snapshot,
                manifest.created_at,
                manifest.expires_at,
            ) != (
                self.plan.org_id,
                self.plan.kg_id,
                self.target.operation_id,
                self.target.attempt_id,
                self.target.job_id,
                descriptor.snapshot,
                descriptor.created_at,
                descriptor.expires_at,
            ):
                raise invalid()
            async for page in reader.pages(manifest):
                batch.extend(part.object for part in page.parts)
                await save()
            batch.extend(manifest.pages)
            # Delete metadata last. Retries after any object deletion use this durable
            # inventory, so missing original/package bytes never trigger an unsafe rescan.
            batch.append(descriptor.manifest)
        else:
            if descriptor.content.size_bytes > self.plan.policy.max_verify_bytes:
                raise DomainError(
                    ErrorCode.QUOTA_EXCEEDED, "Cleanup package exceeds its byte budget."
                )
            async for _ in store.open(descriptor.content.retained):
                await guard()
            batch.append(descriptor.content.retained)
        await save()
        if isinstance(descriptor, BackupSeal):
            # Bound declared object counts and bytes before reading originals.
            await reader.verify(manifest)
        await guard()
        await self.database.retention_inventories.insert_one(
            self.key | {"sealed": True, "count": position, "checksum": checksum.hexdigest()}
        )
        await self.verify()

    def _validate(self, reference: PackageObject) -> None:
        if (reference.org_id, reference.kg_id, reference.backup_id, reference.attempt_id) != (
            self.plan.org_id,
            self.plan.kg_id,
            self.target.operation_id,
            self.target.attempt_id,
        ):
            raise invalid()

    async def verify(self) -> dict[str, Any]:
        meta = await self.database.retention_inventories.find_one(self.key | {"sealed": True})
        if meta is None or type(meta.get("count")) is not int or not 1 <= meta["count"] <= 100000:
            raise invalid()
        checksum, count = hashlib.sha256(), 0
        async for row in (
            self.database.retention_objects.find(self.key).sort("position", 1).limit(100001)
        ):
            count += 1
            reference = PackageObject.model_validate(row["reference"])
            self._validate(reference)
            if row["position"] != count:
                raise invalid()
            checksum.update(reference.model_dump_json().encode() + b"\n")
        if count != meta["count"] or checksum.hexdigest() != meta.get("checksum"):
            raise invalid()
        return meta
