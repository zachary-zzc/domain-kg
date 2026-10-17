"""Model-free curation repair stages immutable projections and publishes by fenced CAS."""

from uuid import uuid5

from pydantic import ValidationError
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import Action
from domain_kg.jobs.models import LeasedJob
from domain_kg.jobs.queue import JobQueue, now
from domain_kg.models import ScopedRef
from domain_kg.operations.audit import AuditTrail
from domain_kg.operations.maintenance_origin import MaintenanceOrigin, MaintenanceResult
from domain_kg.operations.maintenance_preparation import MaintenancePreparation, PreparedMaintenance
from domain_kg.operations.maintenance_results import MaintenanceResults
from domain_kg.revisions.journal import PublicationJournal
from domain_kg.revisions.models import RevisionBatch
from domain_kg.revisions.projections import AcceptedProjection, digest
from domain_kg.revisions.publication import projection_rows, verify_document
from domain_kg.storage.neo4j import GraphProjection
from domain_kg.storage.qdrant import VectorProjection
from domain_kg.storage.router import projection_checksum


class MaintenanceService:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database = queue, queue.database
        self.preparation = MaintenancePreparation(queue)

    async def run(self, job: LeasedJob) -> None:
        if job.kind != "maintenance":
            raise inaccessible_reference()
        if await MaintenanceResults(self.queue).accepted(job):
            return
        try:
            await self._run(job)
        except (ValidationError, KeyError, TypeError, ValueError):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Curation repair metadata is invalid."
            ) from None

    async def _run(self, job: LeasedJob) -> None:
        prepared = await self.preparation.prepare(job)
        lease = await self.queue.acquire_publication(job)
        target, document = prepared.target, prepared.document
        batch = RevisionBatch(
            org_id=job.org_id,
            kg_id=job.kg_id,
            revision_id=target.parent.curation.id,  # type: ignore[union-attr]
            attempt_id=job.attempt_id,
            generation=target.parent.generation,
            manifest_id=uuid5(job.attempt_id, "maintenance-manifest"),
            content_hash=target.parent.content_hash,
            expected_revision=prepared.operation.snapshot.revision,
            records=tuple(
                ScopedRef(org_id=job.org_id, kg_id=job.kg_id, id=row.id) for row in document.rows
            ),
        )
        origin = MaintenanceOrigin(
            org_id=job.org_id,
            kg_id=job.kg_id,
            id=job.target_id,
            job_id=job.id,
            attempt_id=job.attempt_id,
            parent_manifest_id=target.parent.manifest_id,
            manifest_id=batch.manifest_id,
            base=target.base,
        )
        access, _, _ = await self.preparation.guard(job)
        parent = await PublicationJournal(self.queue).attempt(access, target.parent.manifest_id)
        ns = {"org_id": str(job.org_id), "kg_id": str(job.kg_id)}
        value = ns | {
            "id": str(batch.manifest_id),
            "job": job.model_dump(mode="json"),
            "lease": lease.model_dump(mode="json"),
            "curation": target.parent.curation.model_dump(mode="json"),  # type: ignore[union-attr]
            "embedded_checkpoint": parent["embedded_checkpoint"],
            "batch": batch.model_dump(mode="json") | {"records": []},
            "record_count": len(batch.records),
            "record_checksum": projection_checksum([str(ref.id) for ref in batch.records]),
            "state": "prepared",
            "receipts": [],
            "maintenance_origin": origin.model_dump(mode="json"),
        }

        async def stage(session: AsyncClientSession) -> None:
            await self.preparation.guard(job, session)
            await self.queue.validate_publication(job, lease, session)
            previous = await self.database.projection_attempts.find_one(
                ns | {"id": value["id"]}, session=session
            )
            if previous:
                if any(
                    previous.get(key) != value[key]
                    for key in ("batch", "embedded_checkpoint", "maintenance_origin", "job")
                ):
                    raise DomainError(ErrorCode.VERSION_CONFLICT, "Repair attempt changed.")
                return
            await self.database.projection_attempts.insert_one(value, session=session)
            if batch.records:
                await self.database.projection_record_refs.insert_many(
                    [
                        ns
                        | {
                            "manifest_id": value["id"],
                            "position": position,
                            "reference": ref.model_dump(mode="json"),
                        }
                        for position, ref in enumerate(batch.records)
                    ],
                    session=session,
                )

        await self.queue.transactions.transaction(stage)
        stores = await self.queue.router.for_kg(access, job.kg_id)
        rows = projection_rows(document, batch)
        await GraphProjection(stores.graph).write(rows)
        await self.preparation.guard(job)
        await VectorProjection(stores.vectors).write(
            rows, [list(row.vector) for row in document.rows]
        )
        receipts = await verify_document(stores, document, batch)
        await self.preparation.guard(job)
        binding = AcceptedProjection(
            org_id=job.org_id,
            kg_id=job.kg_id,
            curation=document.curation,
            attempt_id=job.attempt_id,
            manifest_id=batch.manifest_id,
            content_hash=batch.content_hash,
            generation=batch.generation,
        )
        result = MaintenanceResult(
            **origin.model_dump(),
            binding=binding,
            published_revision=batch.expected_revision + 1,
            content_hash=batch.content_hash,
            created_at=now(),
        )

        async def publish(session: AsyncClientSession) -> None:
            actor, _, _ = await self.preparation.guard(job, session)
            await self.queue.transactions.fence(actor, Action.CONFIGURE, session)
            await self.queue.validate_publication(job, lease, session)
            await self._configuration(prepared, session)
            prior = (
                await self.database.publication_bindings.find(
                    ns | {"view_manifest_id": str(target.view_manifest_id)}, session=session
                )
                .limit(1001)
                .to_list()
            )
            if len(prior) > 1000:
                raise DomainError(
                    ErrorCode.QUOTA_EXCEEDED, "Repair publication exceeds its binding budget."
                )
            for row in prior:
                copy = {key: item for key, item in row.items() if key != "_id"} | {
                    "view_manifest_id": str(batch.manifest_id)
                }
                if row["owner_key"] == target.owner_key:
                    copy["binding"] = binding.model_dump(mode="json")
                await self.database.publication_bindings.insert_one(copy, session=session)
            await self.database.publication_manifests.insert_one(
                ns
                | {
                    "id": str(batch.manifest_id),
                    "revision": result.published_revision,
                    "generation": str(batch.generation),
                    "state": "accepted",
                    "binding_count": len(prior),
                    "job_id": str(job.id),
                    "attempt_id": str(job.attempt_id),
                    "maintenance_operation_id": str(job.target_id),
                    "created_at": now(),
                },
                session=session,
            )
            changed = await self.database.projection_attempts.update_one(
                ns | {"id": str(batch.manifest_id), "state": "prepared"},
                {
                    "$set": {
                        "state": "accepted",
                        "receipts": [r.model_dump(mode="json") for r in receipts],
                        "receipt": {"published_revision": result.published_revision},
                    }
                },
                session=session,
            )
            if not changed.matched_count:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "Repair readiness changed.")
            await self.database.maintenance_results.insert_one(
                result.model_dump(mode="json") | {"state": "sealed"}, session=session
            )
            changed = await self.database.kgs.update_one(
                {
                    "org_id": ns["org_id"],
                    "id": ns["kg_id"],
                    "published_revision": batch.expected_revision,
                    "published_manifest_id": str(target.view_manifest_id),
                    "publication_fence": lease.fence,
                    "publication_lease.lease_token": str(lease.lease_token),
                },
                {
                    "$set": {
                        "published_manifest_id": str(batch.manifest_id),
                        "publication_lease.lease_until": now(),
                    },
                    "$inc": {"published_revision": 1},
                },
                session=session,
            )
            if not changed.matched_count:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "Repair publication changed.")
            changed = await self.database.jobs.update_one(
                self.queue._active(job),
                {
                    "$set": {
                        "stage": "ready",
                        "progress": 1,
                        "lease_until": now(),
                        "error_code": None,
                        "accepted_manifest_id": str(batch.manifest_id),
                        "accepted_operation_id": str(job.target_id),
                        "accepted_operation_hash": digest(result.model_dump(mode="json")),
                    }
                },
                session=session,
            )
            if not changed.matched_count:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "Repair worker lease changed.")
            await AuditTrail(self.queue).append(
                actor, job.kg_id, job.target_id, job.id, "maintenance", "completed", session
            )

        await self.queue.transactions.transaction(publish)

    async def _configuration(
        self, prepared: PreparedMaintenance, session: AsyncClientSession
    ) -> None:
        document = prepared.document
        selected = await self.database.kg_provider_settings.find_one(
            {"org_id": str(document.org_id), "kg_id": str(document.kg_id)}, session=session
        )
        if selected is None or (
            selected["profile_id"],
            selected["profile_revision"],
            selected["revision"],
            selected["embedding_space_id"],
            selected["embedding_model"],
            selected["embedding_dimensions"],
        ) != (
            str(document.profile.id),
            document.profile.revision,
            document.profile_selection_revision,
            document.profile.space_id,
            document.profile.model,
            document.profile.dimensions,
        ):
            raise DomainError(ErrorCode.VERSION_CONFLICT, "Repair embedding configuration changed.")
