"""Bounded, resumable orchestration of existing source and job commands."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4, uuid5

from pydantic import Field, model_validator
from pymongo import ReturnDocument

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.queue import TERMINAL, JobQueue
from domain_kg.materials.inspection import MaterialInspection
from domain_kg.materials.lifecycle import MaterialLifecycle
from domain_kg.materials.models import (
    RemoveMaterial,
    ReplaceMaterial,
    RestoreMaterial,
    UploadCommand,
)
from domain_kg.materials.service import digest
from domain_kg.models import FrozenModel, IdempotencyKey, Namespace, NonNegative, ScopedRef
from domain_kg.storage.models import MaterialRecord

logger = logging.getLogger(__name__)
BatchOperation = Literal["delete", "restore", "process", "retry", "cancel"]


class BatchTarget(FrozenModel):
    id: UUID
    revision: NonNegative | None = None
    version_id: UUID | None = None


class BatchCommand(FrozenModel):
    operation: BatchOperation
    items: Annotated[tuple[BatchTarget, ...], Field(min_length=1, max_length=100)]
    idempotency_key: IdempotencyKey

    @model_validator(mode="after")
    def distinct_targets(self) -> Self:
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("batch targets must be distinct")
        materials = self.operation in ("delete", "restore", "process")
        if any((item.revision is not None) != materials for item in self.items):
            raise ValueError("material operations require expected revisions")
        if not materials and any(item.version_id is not None for item in self.items):
            raise ValueError("job controls do not accept source versions")
        return self


class BatchItemResult(FrozenModel):
    id: UUID
    title: Annotated[str, Field(max_length=1000)] = ""
    outcome: Literal["pending", "submitted", "completed", "skipped", "failed"]
    job_id: UUID | None = None
    code: str | None = None
    message: str = ""


class BatchResult(Namespace):
    batch_id: UUID
    operation: BatchOperation
    items: tuple[BatchItemResult, ...]
    command: BatchCommand
    running: bool = False


class BatchOperations:
    def __init__(self, queue: JobQueue, lifecycle: MaterialLifecycle) -> None:
        self.queue, self.lifecycle = queue, lifecycle
        self.materials, self.policy = lifecycle.materials, queue.policy
        self.inspection = MaterialInspection(queue)
        self.records = queue.database.batch_operations

    @staticmethod
    def action(operation: BatchOperation) -> Action:
        return (
            Action.REMOVE
            if operation == "delete"
            else (Action.REPLACE if operation in ("process", "restore") else Action.INGEST)
        )

    async def _visible(
        self, access: AccessContext, kg: UUID, item: BatchTarget, operation: BatchOperation
    ) -> str:
        if operation in ("retry", "cancel"):
            receipt = await self.queue.get(access, item.id)
            if receipt.kg_id != kg:
                raise inaccessible_reference()
            return receipt.source.title if receipt.source else receipt.kind or "Job"
        else:
            target = await self.policy._material_acl_record(access, kg, item.id)
            if target is None or target.get("purged"):
                raise inaccessible_reference()
        stores = await self.materials.router.for_sources(access, kg)
        raw = await stores.authority._require(
            "materials", ScopedRef(org_id=access.org_id, kg_id=kg, id=item.id)
        )
        material = MaterialRecord.model_validate(
            {key: value for key, value in raw.items() if key != "_id"}
        )
        return material.title

    async def execute(self, access: AccessContext, kg: UUID, command: BatchCommand) -> BatchResult:
        command = BatchCommand.model_validate(command)
        current = await self.policy.revalidate(access, action=self.action(command.operation))
        if kg not in current.kg_ids:
            raise inaccessible_reference()
        actor = {
            "issuer": current.principal.issuer,
            "subject": current.principal.subject,
            "kind": current.principal.kind,
        }
        identifier = uuid5(
            current.org_id, digest({"kg": str(kg), "actor": actor, "key": command.idempotency_key})
        )
        key = {"org_id": str(current.org_id), "kg_id": str(kg), "id": str(identifier), **actor}
        record_id = f"{current.org_id}:{kg}:{identifier}"
        key["_id"] = record_id
        fingerprint = digest(command.model_dump(mode="json"))
        raw = await self.records.find_one_and_update(
            {"_id": record_id},
            {
                "$setOnInsert": key
                | {
                    "digest": fingerprint,
                    "command": command.model_dump(mode="json"),
                    "results": {},
                    "created_at": datetime.now(UTC),
                }
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        if raw is None or raw["digest"] != fingerprint:
            raise DomainError(ErrorCode.IDEMPOTENCY_CONFLICT, "Batch key has different input.")
        owner = str(uuid4())
        leased = await self.records.find_one_and_update(
            key
            | {
                "$or": [
                    {"lease_until": {"$exists": False}},
                    {"lease_until": {"$lte": datetime.now(UTC)}},
                ]
            },
            {
                "$set": {
                    "lease_owner": owner,
                    "lease_until": datetime.now(UTC) + timedelta(minutes=5),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if leased is None:
            return await self.get(current, kg, identifier)
        try:
            for item in command.items:
                if str(item.id) in leased["results"]:
                    continue
                renewed = await self.records.update_one(
                    key | {"lease_owner": owner},
                    {"$set": {"lease_until": datetime.now(UTC) + timedelta(minutes=5)}},
                )
                if not renewed.matched_count:
                    raise DomainError(
                        ErrorCode.VERSION_CONFLICT, "The batch submission lease changed."
                    )
                try:
                    await self._visible(current, kg, item, command.operation)
                    result = await self._one(
                        current, kg, command.operation, item, str(uuid5(identifier, str(item.id)))
                    )
                except DomainError as error:
                    result = BatchItemResult(
                        id=item.id, outcome="failed", code=error.code, message=error.message
                    )
                except Exception:
                    logger.exception("Batch item failed: batch=%s target=%s", identifier, item.id)
                    result = BatchItemResult(
                        id=item.id,
                        outcome="failed",
                        code=ErrorCode.BACKEND_UNAVAILABLE,
                        message="Processing could not complete.",
                    )
                saved = await self.records.update_one(
                    key | {"lease_owner": owner},
                    {"$set": {f"results.{item.id}": result.model_dump(mode="json")}},
                )
                if not saved.matched_count:
                    raise DomainError(
                        ErrorCode.VERSION_CONFLICT, "The batch submission lease changed."
                    )
        finally:
            await self.records.update_one(
                key | {"lease_owner": owner}, {"$unset": {"lease_owner": "", "lease_until": ""}}
            )
        return await self.get(current, kg, identifier)

    async def get(self, access: AccessContext, kg: UUID, identifier: UUID) -> BatchResult:
        current = await self.policy.revalidate(access, action=Action.READ)
        if kg not in current.kg_ids:
            raise inaccessible_reference()
        raw = await self.records.find_one(
            {
                "_id": f"{current.org_id}:{kg}:{identifier}",
                "org_id": str(current.org_id),
                "kg_id": str(kg),
                "id": str(identifier),
                "issuer": current.principal.issuer,
                "subject": current.principal.subject,
                "kind": current.principal.kind,
            }
        )
        if raw is None:
            raise inaccessible_reference()
        command = BatchCommand.model_validate(raw["command"])
        current = await self.policy.revalidate(current, action=self.action(command.operation))
        results = []
        for item in command.items:
            try:
                title = await self._visible(current, kg, item, command.operation)
                result = (
                    BatchItemResult.model_validate(raw["results"][str(item.id)])
                    if (str(item.id) in raw["results"])
                    else BatchItemResult(id=item.id, outcome="pending")
                )
                if result.id != item.id:
                    raise DomainError(
                        ErrorCode.PROJECTION_INCONSISTENT, "The batch result target differs."
                    )
                result = result.model_copy(update={"title": title})
            except DomainError as error:
                result = BatchItemResult(
                    id=item.id, outcome="failed", code=error.code, message=error.message
                )
            results.append(result)
        await self.policy.revalidate(current)
        return BatchResult(
            org_id=current.org_id,
            kg_id=kg,
            batch_id=identifier,
            operation=command.operation,
            command=command,
            items=tuple(results),
            running=bool(
                raw.get("lease_until")
                and raw["lease_until"].replace(tzinfo=UTC) > datetime.now(UTC)
            ),
        )

    async def _one(
        self,
        access: AccessContext,
        kg: UUID,
        operation: BatchOperation,
        item: BatchTarget,
        key: str,
    ) -> BatchItemResult:
        if operation in ("retry", "cancel"):
            job = await self.queue.get(access, item.id)
            if (
                operation == "retry" and job.stage not in ("failed", "cancelled", "repair_required")
            ) or (
                operation == "cancel" and job.stage in ("ready", "ready_with_warnings", "cancelled")
            ):
                return BatchItemResult(
                    id=item.id,
                    outcome="skipped",
                    job_id=job.job_id,
                    message="The job is already active or complete.",
                )
            receipt = await (self.queue.retry if operation == "retry" else self.queue.cancel)(
                access, item.id, idempotency_key=key
            )
            return BatchItemResult(
                id=item.id,
                outcome="submitted" if operation == "retry" else "completed",
                job_id=receipt.job_id,
            )
        assert item.revision is not None  # BatchCommand validates material revisions.
        if operation == "delete":
            removed = await self.lifecycle.remove(
                access,
                RemoveMaterial(
                    kg_id=kg,
                    material_id=item.id,
                    action="delete",
                    expected_revision=item.revision,
                    idempotency_key=key,
                ),
            )
            return BatchItemResult(id=item.id, outcome="completed", job_id=removed.job_id)
        if operation == "restore":
            restored = await self.lifecycle.restore(
                access,
                RestoreMaterial(
                    kg_id=kg,
                    material_id=item.id,
                    expected_revision=item.revision,
                    idempotency_key=key,
                ),
            )
            return BatchItemResult(
                id=item.id,
                outcome="submitted" if restored.job_id else "completed",
                job_id=restored.job_id,
            )
        target = await self.materials._target(access, kg, item.id)
        if target["revision"] != item.revision:
            raise DomainError(ErrorCode.VERSION_CONFLICT, "The material revision changed.")
        if not target.get("active_version_id"):
            raw = await self.queue.database.jobs.find_one(
                {
                    "org_id": str(access.org_id),
                    "kg_id": str(kg),
                    "material_id": str(item.id),
                    "kind": {"$in": ["ingest", "import", "restore_material", "reprocess_material"]},
                },
                sort=[("created_at", -1), ("id", -1)],
            )
            if raw is None:
                raise DomainError(ErrorCode.REVISION_NOT_READY, "The source has no processing job.")
            job = await self.queue.get(access, UUID(raw["id"]))
            if job.stage in ("failed", "cancelled", "repair_required"):
                await self.queue.retry(access, job.job_id, idempotency_key=key)
                return BatchItemResult(id=item.id, outcome="submitted", job_id=job.job_id)
            return BatchItemResult(
                id=item.id,
                outcome="skipped",
                job_id=job.job_id,
                message="The job is already active or complete.",
            )
        detail = await self.inspection.get(access, kg, item.id, include_unverified=True)
        source = detail.material
        if source.active_version_id != item.version_id or source.revision != item.revision:
            raise DomainError(ErrorCode.VERSION_CONFLICT, "The active version changed.")
        active = await self.queue.database.jobs.find_one(
            {
                "org_id": str(access.org_id),
                "kg_id": str(kg),
                "material_id": str(item.id),
                "kind": "replace",
                "expected_active_version": str(source.active_version_id),
                "stage": {"$nin": list(TERMINAL)},
            }
        )
        if active is not None:
            job = await self.queue.get(access, UUID(active["id"]))
            return BatchItemResult(
                id=item.id,
                outcome="skipped",
                job_id=job.job_id,
                message="The job is already active or complete.",
            )
        original = next(v for v in detail.versions if v.id == source.active_version_id)
        uploaded = await self.materials.upload(
            access,
            UploadCommand(
                kg_id=kg,
                target_material_id=item.id,
                media_type=original.media_type,
                sha256=original.sha256,
                size_bytes=original.size_bytes,
                idempotency_key=key + "-upload",
            ),
            self.materials.original(access, kg, item.id, source.active_version_id),
        )
        metadata = source.metadata
        replacement = await self.materials.replace(
            access,
            ReplaceMaterial(
                kg_id=kg,
                material_id=item.id,
                expected_active_version=source.active_version_id,
                expected_revision=item.revision,
                title=source.title,
                upload_id=uploaded.upload_id,
                tags=metadata.tags,
                author=metadata.author,
                source=metadata.source,
                language=metadata.language,
                idempotency_key=key,
            ),
        )
        return BatchItemResult(id=item.id, outcome="submitted", job_id=replacement.job_id)
