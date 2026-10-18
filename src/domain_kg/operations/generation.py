"""Serial model-free rebuild; only the final fenced MongoDB commit changes acceptance."""

from dataclasses import dataclass
from functools import partial
from typing import Any
from uuid import UUID, uuid5

from pydantic import ValidationError
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import Action
from domain_kg.jobs.models import LeasedJob, PublicationLease
from domain_kg.jobs.queue import JobQueue, now
from domain_kg.models import Namespace, ScopedRef
from domain_kg.operations.audit import AuditTrail
from domain_kg.operations.generation_capture import GenerationCapture
from domain_kg.operations.generation_origin import GenerationOrigin, GenerationResult
from domain_kg.operations.generation_preparation import GenerationPreparation
from domain_kg.operations.generation_results import GenerationResults
from domain_kg.operations.generation_targets import GenerationInput, GenerationTarget, invalid
from domain_kg.operations.models import OwnerOperation
from domain_kg.revisions.journal import PublicationJournal
from domain_kg.revisions.models import ProjectionReceipt, RevisionBatch
from domain_kg.revisions.projections import AcceptedProjection, digest
from domain_kg.revisions.publication import projection_rows, verify_document
from domain_kg.revisions.writes import ProjectionWrites
from domain_kg.storage.neo4j import GraphProjection
from domain_kg.storage.qdrant import VectorProjection
from domain_kg.storage.router import RoutedStores, projection_checksum


@dataclass(frozen=True)
class StagedGeneration:
    item: GenerationInput
    binding: AcceptedProjection
    batch: RevisionBatch
    origin: GenerationOrigin
    receipts: tuple[ProjectionReceipt, ...]


class GenerationService:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database = queue, queue.database
        self.capture, self.preparation = GenerationCapture(queue), GenerationPreparation(queue)

    async def run(self, job: LeasedJob) -> None:
        if job.kind != "generation":
            raise inaccessible_reference()
        if await GenerationResults(self.queue).accepted(job):
            return
        try:
            await self._run(job)
        except (ValidationError, KeyError, TypeError, ValueError):
            raise invalid() from None
        finally:
            await self.capture.release(job)

    async def _run(self, job: LeasedJob) -> None:
        inputs = await self.capture.start(job)
        access, operation, target = await self.capture.guard(job)
        stores = await self.queue.router._bind_namespace(
            Namespace(org_id=job.org_id, kg_id=job.kg_id), target.profile, target.generation
        )
        await self.capture.guard(job)
        await ProjectionWrites(self.queue).run(
            job,
            target.generation,
            None,
            partial(self.queue.router._provision_namespace, stores, self.queue.router.dimensions),
        )
        await self.capture.guard(job)
        lease = await self.queue.acquire_publication(job)
        root_id = uuid5(job.attempt_id, "generation-root")
        await PublicationJournal(self.queue).initialize()
        await self.database.generation_outputs.create_index(
            [("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True
        )
        staged = []
        for item in inputs:
            staged.append(await self._stage(job, item, target, operation, lease, root_id, stores))
        # A receipt proves readiness for its exact contents. Re-read all required
        # target records after preparation, keeping only one document in memory.
        for value in staged:
            document, _ = await self.preparation.document(job, value.item)
            if await verify_document(stores, document, value.batch) != value.receipts:
                raise invalid()
        await self._publish(job, operation, target, lease, root_id, tuple(staged))

    async def _stage(
        self,
        job: LeasedJob,
        item: GenerationInput,
        target: GenerationTarget,
        operation: OwnerOperation,
        lease: PublicationLease,
        root_id: UUID,
        stores: RoutedStores,
    ) -> StagedGeneration:
        document, checkpoint = await self.preparation.document(job, item)
        revision_id = (
            item.parent.source.version_id
            if item.parent.source
            else (item.parent.curation.id if item.parent.curation else None)
        )
        if revision_id is None:
            raise invalid()
        batch = RevisionBatch(
            org_id=job.org_id,
            kg_id=job.kg_id,
            revision_id=revision_id,
            attempt_id=job.attempt_id,
            generation=target.generation,
            manifest_id=uuid5(job.attempt_id, f"generation/{item.id}"),
            content_hash=item.parent.content_hash,
            expected_revision=operation.snapshot.revision,
            records=tuple(
                ScopedRef(org_id=job.org_id, kg_id=job.kg_id, id=row.id) for row in document.rows
            ),
        )
        origin = GenerationOrigin(
            org_id=job.org_id,
            kg_id=job.kg_id,
            id=job.target_id,
            job_id=job.id,
            attempt_id=job.attempt_id,
            input_id=item.id,
            manifest_id=batch.manifest_id,
            root_manifest_id=root_id,
            parent_manifest_id=item.parent.manifest_id,
            base=item.base,
        )
        binding = item.parent.model_copy(
            update={
                "attempt_id": job.attempt_id,
                "generation": target.generation,
                "manifest_id": batch.manifest_id,
            }
        )
        ns = {"org_id": str(job.org_id), "kg_id": str(job.kg_id)}
        if binding.source is None and binding.curation is None:
            raise invalid()
        value: dict[str, Any] = ns | {
            "id": str(batch.manifest_id),
            "job": job.model_dump(mode="json"),
            "lease": lease.model_dump(mode="json"),
            "embedded_checkpoint": checkpoint.model_dump(mode="json"),
            "batch": batch.model_dump(mode="json") | {"records": []},
            "record_count": len(batch.records),
            "record_checksum": projection_checksum([str(ref.id) for ref in batch.records]),
            "state": "prepared",
            "write_tracking_version": 1,
            "receipts": [],
            "generation_origin": origin.model_dump(mode="json"),
            **(
                {"source": binding.source.model_dump(mode="json")}
                if binding.source
                else {
                    "curation": binding.curation.model_dump(mode="json")
                    if binding.curation
                    else None
                }
            ),
        }
        if binding.import_origin:
            value["import_origin"] = binding.import_origin.model_dump(mode="json")

        async def save(session: AsyncClientSession) -> None:
            await self.capture.guard(job, session)
            await self.queue.validate_publication(job, lease, session)
            old = await self.database.projection_attempts.find_one(
                ns | {"id": value["id"]}, session=session
            )
            if old:
                if any(
                    old.get(key) != value[key]
                    for key in ("batch", "job", "generation_origin", "embedded_checkpoint")
                ):
                    raise invalid()
                return
            await self.database.projection_attempts.insert_one(value, session=session)
            if batch.records:
                await self.database.projection_record_refs.insert_many(
                    [
                        ns
                        | {
                            "manifest_id": value["id"],
                            "position": i,
                            "reference": ref.model_dump(mode="json"),
                        }
                        for i, ref in enumerate(batch.records)
                    ],
                    session=session,
                )

        await self.queue.transactions.transaction(save)
        rows = projection_rows(document, batch)

        async def write() -> None:
            await self.capture.guard(job)
            await GraphProjection(stores.graph).write(rows)
            await self.capture.guard(job)
            await VectorProjection(stores.vectors).write(
                rows, [list(row.vector) for row in document.rows]
            )

        await ProjectionWrites(self.queue).run(job, batch.generation, batch.manifest_id, write)
        await self.capture.guard(job)
        receipts = await verify_document(stores, document, batch)
        await self.capture.guard(job)
        return StagedGeneration(item, binding, batch, origin, receipts)

    async def _publish(
        self,
        job: LeasedJob,
        operation: OwnerOperation,
        target: GenerationTarget,
        lease: PublicationLease,
        root_id: UUID,
        staged: tuple[StagedGeneration, ...],
    ) -> None:
        result = GenerationResult(
            org_id=job.org_id,
            kg_id=job.kg_id,
            id=job.target_id,
            job_id=job.id,
            attempt_id=job.attempt_id,
            root_manifest_id=root_id,
            generation=target.generation,
            published_revision=operation.snapshot.revision + 1,
            inputs_hash=target.inputs_hash,
            binding_count=len(staged),
            created_at=now(),
            bindings_hash=digest(
                [
                    {
                        "owner_key": value.item.owner_key,
                        "binding": value.binding.model_dump(mode="json"),
                    }
                    for value in staged
                ]
            ),
        )
        ns = {"org_id": str(job.org_id), "kg_id": str(job.kg_id)}

        async def publish(session: AsyncClientSession) -> None:
            actor, _, current_target = await self.capture.guard(job, session)
            await self.queue.transactions.fence(actor, Action.CONFIGURE, session)
            await self.queue.validate_publication(job, lease, session)
            inputs = await self.capture.check_all(current_target, session)
            if tuple(value.item for value in staged) != inputs or current_target != target:
                raise invalid()
            for value in staged:
                await self.database.publication_bindings.insert_one(
                    ns
                    | {
                        "view_manifest_id": str(root_id),
                        "owner_key": value.item.owner_key,
                        "binding": value.binding.model_dump(mode="json"),
                    },
                    session=session,
                )
                changed = await self.database.projection_attempts.update_one(
                    ns
                    | {
                        "id": str(value.batch.manifest_id),
                        "state": "prepared",
                        "generation_origin": value.origin.model_dump(mode="json"),
                    },
                    {
                        "$set": {
                            "state": "accepted",
                            "receipts": [item.model_dump(mode="json") for item in value.receipts],
                            "receipt": {"published_revision": result.published_revision},
                        }
                    },
                    session=session,
                )
                if not changed.matched_count:
                    raise invalid()
                await self.database.generation_outputs.insert_one(
                    ns
                    | {
                        "id": str(value.batch.manifest_id),
                        "operation_id": str(job.target_id),
                        "input_id": str(value.item.id),
                        "owner_key": value.item.owner_key,
                        "binding": value.binding.model_dump(mode="json"),
                        "origin": value.origin.model_dump(mode="json"),
                        "state": "sealed",
                    },
                    session=session,
                )
            await self.database.publication_manifests.insert_one(
                ns
                | {
                    "id": str(root_id),
                    "revision": result.published_revision,
                    "generation": str(target.generation),
                    "state": "accepted",
                    "binding_count": len(staged),
                    "job_id": str(job.id),
                    "attempt_id": str(job.attempt_id),
                    "generation_operation_id": str(job.target_id),
                    "created_at": now(),
                },
                session=session,
            )
            await self.database.generation_results.insert_one(
                result.model_dump(mode="json") | {"state": "sealed"}, session=session
            )
            changed = await self.database.kgs.update_one(
                {
                    "org_id": ns["org_id"],
                    "id": ns["kg_id"],
                    "published_revision": operation.snapshot.revision,
                    "generation": str(target.parent_generation),
                    "published_manifest_id": str(target.parent_manifest_id)
                    if target.parent_manifest_id
                    else None,
                    "publication_fence": lease.fence,
                    "publication_lease.lease_token": str(lease.lease_token),
                    "visibility_revision": operation.snapshot.visibility_revision,
                    "configuration_epoch": operation.snapshot.configuration_epoch
                    if operation.snapshot.configuration_epoch
                    else {"$in": [0, None]},
                },
                {
                    "$set": {
                        "generation": str(target.generation),
                        "published_manifest_id": str(root_id),
                        "publication_lease.lease_until": now(),
                    },
                    "$inc": {"published_revision": 1, "configuration_epoch": 1},
                },
                session=session,
            )
            if not changed.matched_count:
                raise DomainError(
                    ErrorCode.VERSION_CONFLICT, "The accepted rebuild boundary changed."
                )
            await self.database.projection_generations.update_one(
                ns | {"id": str(target.parent_generation)},
                {"$set": {"retired_at": now()}},
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
                        "accepted_manifest_id": str(root_id),
                        "accepted_operation_id": str(job.target_id),
                        "accepted_operation_hash": digest(result.model_dump(mode="json")),
                    }
                },
                session=session,
            )
            if not changed.matched_count:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The rebuild worker lease changed.")
            await AuditTrail(self.queue).append(
                actor, job.kg_id, job.target_id, job.id, "generation", "completed", session
            )

        await self.queue.transactions.transaction(publish)
