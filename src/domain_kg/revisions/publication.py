"""Immutable projection attempts and a single MongoDB acceptance point."""

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

from pydantic import ValidationError
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.schema import schema_for_kg
from domain_kg.identity.models import AccessContext, Principal
from domain_kg.imports.provenance import check_import_origin
from domain_kg.ingestion.models import ParsedDocument
from domain_kg.ingestion.resolved import ResolvedDocument
from domain_kg.jobs.models import Checkpoint, LeasedJob, PublicationLease
from domain_kg.jobs.queue import JobQueue, now
from domain_kg.materials.models import BlobRef, MaterialMetadata, RestoreReadPermit
from domain_kg.materials.restoration import reprocessing_permit, restoration_permit
from domain_kg.models import Namespace, ScopedRef, SourceVersion
from domain_kg.providers.embeddings import EmbeddingProfile
from domain_kg.providers.profiles import embedding_space_id
from domain_kg.providers.service import ProviderService
from domain_kg.revisions.journal import PublicationJournal, read_artifact
from domain_kg.revisions.models import (
    Backend,
    ProjectionReceipt,
    PublishRevision,
    ReadinessReport,
    RevisionBatch,
    RevisionReceipt,
)
from domain_kg.revisions.projections import (
    AcceptedProjection,
    EmbeddedDocument,
    ProjectionBody,
    ProjectionDocument,
    ProjectionRow,
)
from domain_kg.storage.neo4j import GraphProjection, inconsistent
from domain_kg.storage.qdrant import VectorProjection
from domain_kg.storage.router import RoutedStores, projection_checksum


def source_of(document: SourceVersion | BlobRef) -> SourceVersion:
    return SourceVersion(
        **{field: getattr(document, field) for field in SourceVersion.model_fields}
    )


def projection_rows(document: ProjectionDocument, batch: RevisionBatch) -> list[dict[str, Any]]:
    return [
        row.payload(
            attempt=batch.attempt_id,
            manifest=batch.manifest_id,
            generation=batch.generation,
            content_hash=batch.content_hash,
            profile=document.profile,
        )
        for row in document.rows
    ]


def receipt_for(
    batch: RevisionBatch, backend: Backend, rows: list[dict[str, Any]]
) -> ProjectionReceipt:
    return ProjectionReceipt(
        org_id=batch.org_id,
        kg_id=batch.kg_id,
        attempt_id=batch.attempt_id,
        generation=batch.generation,
        manifest_id=batch.manifest_id,
        content_hash=batch.content_hash,
        backend=backend,
        record_count=len(rows),
        checksum=projection_checksum([row["projection_id"] for row in rows]),
    )


async def verify_document(
    stores: RoutedStores, document: ProjectionDocument, batch: RevisionBatch
) -> tuple[ProjectionReceipt, ...]:
    rows = projection_rows(document, batch)
    receipts = tuple(receipt_for(batch, name, rows) for name in ("graph", "vector", "lexical"))
    graph, vectors = GraphProjection(stores.graph), VectorProjection(stores.vectors)
    try:
        if (
            not await graph.verify(rows, lexical=True)
            or not await vectors.verify(rows, [list(row.vector) for row in document.rows])
            or not await stores.graph.verify(receipts[0])
            or not await stores.vectors.verify(receipts[1])
        ):
            raise inconsistent()
    except DomainError:
        raise
    except Exception as exc:
        raise DomainError(
            ErrorCode.BACKEND_UNAVAILABLE, "A projection could not be verified.", retryable=True
        ) from exc
    return receipts


async def verify_accepted(
    queue: JobQueue, access: AccessContext, binding: AcceptedProjection
) -> None:
    try:
        await _verify_accepted(queue, access, binding)
    except (ValidationError, KeyError, TypeError):
        raise inconsistent() from None
    except DomainError as error:
        if error.code == ErrorCode.EVIDENCE_VALIDATION_FAILED:
            raise inconsistent() from None
        raise


async def _verify_accepted(
    queue: JobQueue, access: AccessContext, binding: AcceptedProjection
) -> None:
    document, batch, expected = await accepted_document(queue, access, binding)
    stores = await queue.router.for_kg(access, binding.kg_id)
    receipts = await verify_document(stores, document, batch)
    if receipts != expected:
        raise inconsistent()


async def accepted_document(
    queue: JobQueue,
    access: AccessContext,
    binding: AcceptedProjection,
    *,
    restoration: RestoreReadPermit | None = None,
) -> tuple[ProjectionDocument, RevisionBatch, tuple[ProjectionReceipt, ...]]:
    """Authenticate the sealed source independently of derived-backend availability."""
    try:
        return await _accepted_document(queue, access, binding, restoration=restoration)
    except (ValidationError, KeyError, TypeError):
        raise inconsistent() from None
    except DomainError as error:
        if error.code == ErrorCode.EVIDENCE_VALIDATION_FAILED:
            raise inconsistent() from None
        raise


async def _accepted_document(
    queue: JobQueue,
    access: AccessContext,
    binding: AcceptedProjection,
    *,
    restoration: RestoreReadPermit | None = None,
) -> tuple[ProjectionDocument, RevisionBatch, tuple[ProjectionReceipt, ...]]:
    journal = PublicationJournal(queue)
    attempt = await journal.attempt(access, binding.manifest_id)
    batch = RevisionBatch.model_validate(attempt["batch"])
    if attempt["state"] != "accepted":
        raise inconsistent()
    if (
        batch.org_id,
        batch.kg_id,
        batch.attempt_id,
        batch.generation,
        batch.manifest_id,
        batch.content_hash,
    ) != (
        binding.org_id,
        binding.kg_id,
        binding.attempt_id,
        binding.generation,
        binding.manifest_id,
        binding.content_hash,
    ):
        raise inconsistent()
    if attempt.get("generation_origin"):
        from domain_kg.operations.generation_results import generation_base

        await generation_base(queue, access, attempt)
    if binding.source is None:
        from domain_kg.revisions.curation_publication import curation_document

        if (
            binding.curation is None
            or restoration is not None
            or (ScopedRef.model_validate(attempt.get("curation")) != binding.curation)
        ):
            raise inconsistent()
        return (
            await curation_document(queue, access, attempt),
            batch,
            tuple(ProjectionReceipt.model_validate(item) for item in attempt["receipts"]),
        )
    checkpoint = Checkpoint.model_validate(attempt["embedded_checkpoint"])
    body = await read_artifact(queue, access, checkpoint, binding.source, restoration=restoration)
    if hashlib.sha256(body).hexdigest() != batch.content_hash:
        raise inconsistent()
    document = EmbeddedDocument.model_validate_json(body)
    if (
        source_of(document) != binding.source
        or document.chunk_checkpoint.artifact_id != binding.chunk_artifact_id
        or document.import_origin != binding.import_origin
        or attempt.get("import_origin")
        != (document.import_origin.model_dump(mode="json") if document.import_origin else None)
    ):
        raise inconsistent()
    await check_import_origin(queue.database, binding.source, document.import_origin)
    return (
        document,
        batch,
        tuple(ProjectionReceipt.model_validate(item) for item in attempt["receipts"]),
    )


class ProjectionCoordinator:
    def __init__(self, queue: JobQueue, providers: ProviderService) -> None:
        self.queue, self.providers = queue, providers
        self.journal, self.database = PublicationJournal(queue), queue.database

    async def embed(
        self, job: LeasedJob, resolved_checkpoint: Checkpoint, *, input_hash: str | None = None
    ) -> Checkpoint:
        access = await self.queue.authorize(job)
        permit = await reprocessing_permit(self.queue, access, job)
        raw = await self.queue._current(job)
        source = SourceVersion(
            org_id=job.org_id,
            kg_id=job.kg_id,
            material_id=raw["material_id"],
            version_id=job.target_id,
        )
        resolved = ResolvedDocument.model_validate_json(
            await read_artifact(self.queue, access, resolved_checkpoint, source, restoration=permit)
        )
        if source_of(resolved) != source:
            raise inaccessible_reference()
        chunk_raw = await self.database.job_checkpoints.find_one(
            {
                "org_id": str(job.org_id),
                "kg_id": str(job.kg_id),
                "job_id": str(job.id),
                "artifact_id": str(resolved.extraction.chunk_artifact_id),
                "stage": "chunking",
            }
        )
        if chunk_raw is None:
            raise inaccessible_reference()
        chunk_checkpoint = Checkpoint.model_validate(
            {key: chunk_raw[key] for key in Checkpoint.model_fields}
        )
        parsed = ParsedDocument.model_validate_json(
            await read_artifact(self.queue, access, chunk_checkpoint, source, restoration=permit)
        )
        if source_of(parsed.blob) != source or {chunk.id for chunk in parsed.chunks} != {
            batch.chunk_id for batch in resolved.batches
        }:
            raise inconsistent()
        namespace: dict[str, Any] = {"org_id": source.org_id, "kg_id": source.kg_id}
        bodies = [
            ProjectionBody(**namespace, source=source, id=chunk.id, kind="chunk", chunk=chunk)
            for chunk in parsed.chunks
        ]
        supports: dict[UUID, ProjectionBody] = {}
        for batch in sorted(resolved.batches, key=lambda item: str(item.chunk_id)):
            bodies.extend(
                ProjectionBody(
                    **namespace, source=source, id=item.id, kind="observation", observation=item
                )
                for item in batch.entities
            )
            observations = {item.id: item for item in batch.entities}
            for item in batch.assertions:
                subject = (
                    observations.get(item.subject_observation_id)
                    if item.subject_observation_id
                    else None
                )
                target = (
                    observations.get(item.target_observation_id)
                    if item.target_observation_id
                    else None
                )
                if subject is None or target is None:
                    raise inconsistent()
                supports.setdefault(
                    item.support.id,
                    ProjectionBody(
                        **namespace,
                        source=source,
                        id=item.support.id,
                        kind="support",
                        assertion=item,
                        subject_observation=subject,
                        target_observation=target,
                    ),
                )
        bodies.extend(supports.values())
        selected = await self.providers.selected(access, job.kg_id)
        schema = await schema_for_kg(self.queue.policy, access, job.kg_id)
        extraction = resolved.extraction
        if (selected["profile_id"], selected["profile_revision"], selected["revision"], schema) != (
            str(extraction.profile_id),
            extraction.profile_revision,
            extraction.selection_revision,
            extraction.domain_schema,
        ):
            raise DomainError(ErrorCode.VERSION_CONFLICT, "Extraction configuration changed.")
        configured, embedding_connection = self.providers.registry.resolve_embedding(
            UUID(selected["profile_id"]), Namespace(**namespace), restricted=False
        )
        space_id = embedding_space_id(configured, embedding_connection)
        if space_id != selected.get("embedding_space_id"):
            raise DomainError(ErrorCode.VERSION_CONFLICT, "Embedding configuration changed.")
        expected_profile = EmbeddingProfile(
            **namespace,
            id=configured.id,
            revision=configured.revision,
            model=configured.embedding_model,
            dimensions=configured.embedding_dimensions,
            space_id=space_id,
        )
        input_hash = (
            input_hash
            or hashlib.sha256(
                (
                    resolved_checkpoint.output_hash
                    + expected_profile.model_dump_json()
                    + "/embed-v1"
                ).encode()
            ).hexdigest()
        )
        cached = await self.queue.load_checkpoint(job, "embedding", input_hash)
        if cached is not None:
            previous = EmbeddedDocument.model_validate_json(
                await read_artifact(self.queue, access, cached, source, restoration=permit)
            )
            if (
                source_of(previous),
                previous.profile,
                previous.schema_id,
                previous.schema_revision,
                previous.profile_selection_revision,
            ) != (source, expected_profile, schema.id, schema.revision, selected["revision"]):
                raise inconsistent()
            return cached
        from domain_kg.ingestion.delta import accepted_delta

        await self.providers.authorize_source(access, source, restoration=permit)
        delta = await accepted_delta(self.queue, job, parsed)
        reused = (
            {(row.kind, row.text): list(row.vector) for row in delta.embedded.rows}
            if delta and delta.embedded.profile == expected_profile
            else {}
        )
        missing = [body for body in bodies if (body.kind, body.text) not in reused]
        profile = expected_profile
        if missing:
            profile, generated = await self.providers.embeddings(
                access,
                Namespace(**namespace),
                [body.text for body in missing],
                source=source,
                restoration=permit,
            )
            fresh = iter(generated)
        else:
            fresh = iter([])
        vectors = [
            reused[(body.kind, body.text)] if (body.kind, body.text) in reused else next(fresh)
            for body in bodies
        ]
        await self.providers.authorize_source(access, source, restoration=permit)
        await self.queue.authorize(job)
        if profile != expected_profile or profile.dimensions != self.queue.router.dimensions:
            raise DomainError(ErrorCode.VERSION_CONFLICT, "Embedding generation dimensions differ.")
        document = EmbeddedDocument(
            **source.model_dump(),
            chunk_checkpoint=chunk_checkpoint,
            resolved_checkpoint=resolved_checkpoint,
            profile=profile,
            profile_selection_revision=selected["revision"],
            schema_id=schema.id,
            schema_revision=schema.revision,
            rows=tuple(
                ProjectionRow(**body.model_dump(), vector=tuple(vector))
                for body, vector in zip(bodies, vectors, strict=True)
            ),
            warnings=tuple(
                sorted(
                    set(
                        parsed.warnings
                        + tuple(warning for batch in resolved.batches for warning in batch.warnings)
                    )
                )
            ),
        )
        return await self.queue.store_artifact(
            job, "embedding", input_hash, document.model_dump_json().encode()
        )

    async def _document(self, access: AccessContext, attempt: dict[str, Any]) -> ProjectionDocument:
        if attempt.get("curation"):
            from domain_kg.revisions.curation_publication import curation_document

            return await curation_document(self.queue, access, attempt, leased=True)
        source = SourceVersion.model_validate(attempt["source"])
        checkpoint = Checkpoint.model_validate(attempt["embedded_checkpoint"])
        job = LeasedJob.model_validate(attempt["job"])
        data = await read_artifact(
            self.queue,
            access,
            checkpoint,
            source,
            restoration=await restoration_permit(self.queue, access, job),
        )
        if hashlib.sha256(data).hexdigest() != attempt["batch"]["content_hash"]:
            raise inconsistent()
        document = EmbeddedDocument.model_validate_json(data)
        if source_of(document) != source:
            raise inconsistent()
        return document

    async def prepare(self, job: LeasedJob, embedded_checkpoint: Checkpoint) -> RevisionBatch:
        if job.kind == "curate":
            from domain_kg.revisions.curation_publication import CurationCoordinator

            return await CurationCoordinator(self.queue, self.providers).prepare(
                job, embedded_checkpoint
            )
        await self.journal.initialize()
        access = await self.queue.authorize(job)
        await self.queue.artifact_blob(job, embedded_checkpoint)
        raw = await self.queue._current(job)
        source = SourceVersion(
            org_id=job.org_id,
            kg_id=job.kg_id,
            material_id=raw["material_id"],
            version_id=job.target_id,
        )
        document = EmbeddedDocument.model_validate_json(
            await read_artifact(
                self.queue,
                access,
                embedded_checkpoint,
                source,
                restoration=await restoration_permit(self.queue, access, job),
            )
        )
        if source_of(document) != source or embedded_checkpoint.stage != "embedding":
            raise inaccessible_reference()
        await check_import_origin(self.database, source, document.import_origin)
        lease = await self.queue.acquire_publication(job)
        kg = await self.database.kgs.find_one({"org_id": str(job.org_id), "id": str(job.kg_id)})
        if kg is None:
            raise inaccessible_reference()
        batch = RevisionBatch(
            org_id=job.org_id,
            kg_id=job.kg_id,
            revision_id=uuid5(job.attempt_id, "revision"),
            attempt_id=job.attempt_id,
            generation=kg["generation"],
            manifest_id=uuid5(job.attempt_id, "manifest"),
            content_hash=embedded_checkpoint.output_hash,
            expected_revision=kg["published_revision"],
            records=tuple(
                ScopedRef(org_id=job.org_id, kg_id=job.kg_id, id=row.id) for row in document.rows
            ),
        )
        record: dict[str, Any] = {
            "org_id": str(job.org_id),
            "kg_id": str(job.kg_id),
            "id": str(batch.manifest_id),
            "job": job.model_dump(mode="json"),
            "lease": lease.model_dump(mode="json"),
            "source": source.model_dump(mode="json"),
            "embedded_checkpoint": embedded_checkpoint.model_dump(mode="json"),
            "batch": batch.model_dump(mode="json") | {"records": []},
            "record_count": len(batch.records),
            "record_checksum": projection_checksum(
                [str(reference.id) for reference in batch.records]
            ),
            "state": "prepared",
            "receipts": [],
            "repair_parent_manifest_id": raw.get("repair_parent_manifest_id"),
        }
        if document.import_origin is not None:
            record["import_origin"] = document.import_origin.model_dump(mode="json")
        if job.kind in ("repair", "restore_material"):
            parent = await self.journal.attempt(access, UUID(raw["repair_parent_manifest_id"]))
            if (
                parent["state"] != "accepted"
                or parent["source"] != source.model_dump(mode="json")
                or (parent["batch"]["content_hash"], parent["batch"]["generation"])
                != (batch.content_hash, str(batch.generation))
            ):
                raise inconsistent()

        async def save(session: AsyncClientSession) -> None:
            await self.queue.validate_publication(job, lease, session)
            await self.queue.transactions.fence(access, access.action, session)
            query = {key: record[key] for key in ("org_id", "kg_id", "id")}
            previous = await self.database.projection_attempts.find_one(query, session=session)
            if previous:
                if any(
                    previous[key] != record[key]
                    for key in ("batch", "source", "embedded_checkpoint")
                ):
                    raise DomainError(ErrorCode.VERSION_CONFLICT, "This attempt is already sealed.")
            else:
                await self.database.projection_attempts.insert_one(record, session=session)
                if batch.records:
                    await self.database.projection_record_refs.insert_many(
                        [
                            {
                                "org_id": str(batch.org_id),
                                "kg_id": str(batch.kg_id),
                                "manifest_id": str(batch.manifest_id),
                                "position": position,
                                "reference": reference.model_dump(mode="json"),
                            }
                            for position, reference in enumerate(batch.records)
                        ],
                        session=session,
                    )
            updated = await self.database.jobs.update_one(
                self.queue._active(job), {"$inc": {"projection_epoch": 1}}, session=session
            )
            if not updated.matched_count:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "Worker lease changed.")

        await self.queue.transactions.transaction(save)
        return batch

    async def stage(self, access: AccessContext, batch: RevisionBatch) -> ProjectionReceipt:
        batch = RevisionBatch.model_validate(batch)
        attempt = await self.journal.attempt(access, batch.manifest_id)
        if (
            RevisionBatch.model_validate(attempt["batch"]) != batch
            or attempt["state"] == "accepted"
        ):
            raise DomainError(ErrorCode.VERSION_CONFLICT, "This attempt cannot be staged.")
        job, lease = (
            LeasedJob.model_validate(attempt["job"]),
            PublicationLease.model_validate(attempt["lease"]),
        )
        actor = await self.queue.authorize(job)
        if access.principal != actor.principal:
            raise inaccessible_reference()
        await self.queue.validate_publication(job, lease)
        document = await self._document(actor, attempt)
        stores = await self.queue.router.for_kg(actor, batch.kg_id)
        rows = projection_rows(document, batch)
        await self.database.projection_attempts.update_one(
            {
                "org_id": str(batch.org_id),
                "kg_id": str(batch.kg_id),
                "id": str(batch.manifest_id),
                "state": {"$ne": "accepted"},
            },
            {"$set": {"state": "projecting"}},
        )
        await GraphProjection(stores.graph).write(rows)
        await VectorProjection(stores.vectors).write(
            rows, [list(row.vector) for row in document.rows]
        )
        await self.queue.validate_publication(job, lease)
        return receipt_for(batch, "graph", rows)

    async def verify(self, access: AccessContext, receipt: ProjectionReceipt) -> ReadinessReport:
        attempt = await self.journal.attempt(access, receipt.manifest_id)
        batch = RevisionBatch.model_validate(attempt["batch"])
        job, lease = (
            LeasedJob.model_validate(attempt["job"]),
            PublicationLease.model_validate(attempt["lease"]),
        )
        await self.queue.validate_publication(job, lease)
        actor = await self.queue.authorize(job)
        if actor.principal != access.principal:
            raise inaccessible_reference()
        document = await self._document(actor, attempt)
        rows = projection_rows(document, batch)
        if receipt != receipt_for(batch, "graph", rows):
            raise inaccessible_reference()
        stores = await self.queue.router.for_kg(actor, batch.kg_id)
        receipts = await verify_document(stores, document, batch)
        await self.queue.validate_publication(job, lease)
        await self.database.projection_attempts.update_one(
            {
                "org_id": str(batch.org_id),
                "kg_id": str(batch.kg_id),
                "id": str(batch.manifest_id),
                "state": {"$ne": "accepted"},
            },
            {
                "$set": {
                    "state": "verified",
                    "receipts": [item.model_dump(mode="json") for item in receipts],
                }
            },
        )
        return ReadinessReport(batch=batch, receipts=receipts, ready=True)

    async def publish(self, access: AccessContext, command: PublishRevision) -> RevisionReceipt:
        command = PublishRevision.model_validate(command)
        candidate = await self.journal.attempt(access, command.report.batch.manifest_id)
        if candidate.get("curation"):
            from domain_kg.revisions.curation_publication import CurationCoordinator

            return await CurationCoordinator(self.queue, self.providers).publish(access, command)
        try:
            return await self._publish(access, command)
        except DomainError as error:
            if error.code != ErrorCode.VERSION_CONFLICT:
                raise
            latest = await self.journal.attempt(access, command.report.batch.manifest_id)
            command_hash = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
            if latest["state"] != "accepted" or latest.get("publish_command_hash") != command_hash:
                raise
            job = LeasedJob.model_validate(latest["job"])
            current = await self.queue.policy.revalidate(
                access, action=self.queue._action(job.kind)
            )
            source = SourceVersion.model_validate(latest["source"])
            raw = await self.database.jobs.find_one(self.queue._scope(job))
            if (
                raw is None
                or current.principal != Principal.model_validate(raw["actor"])
                or not await self.queue.policy.material_visible(
                    current, source.kg_id, source.material_id
                )
            ):
                raise inaccessible_reference() from None
            return RevisionReceipt.model_validate(latest["receipt"])

    async def _publish(self, access: AccessContext, command: PublishRevision) -> RevisionReceipt:
        command = PublishRevision.model_validate(command)
        if command.expected_target_revisions:
            raise DomainError(
                ErrorCode.VALIDATION_ERROR,
                "Source publication does not accept curation target revisions.",
            )
        command_hash = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
        batch = command.report.batch
        attempt = await self.journal.attempt(access, batch.manifest_id)
        if RevisionBatch.model_validate(attempt["batch"]) != batch:
            raise inaccessible_reference()
        job, lease = (
            LeasedJob.model_validate(attempt["job"]),
            PublicationLease.model_validate(attempt["lease"]),
        )
        if (command.lease_token, command.fence) != (lease.lease_token, lease.fence):
            raise DomainError(ErrorCode.VERSION_CONFLICT, "Publication lease changed.")
        current = await self.queue.policy.revalidate(access, action=self.queue._action(job.kind))
        source = SourceVersion.model_validate(attempt["source"])
        if not await self.queue.policy.material_visible(current, job.kg_id, source.material_id):
            if job.kind not in ("restore_material", "reprocess_material"):
                raise inaccessible_reference()
            await self.queue.authorize(job)
        raw_job = await self.database.jobs.find_one(
            {"org_id": str(job.org_id), "kg_id": str(job.kg_id), "id": str(job.id)}
        )
        if raw_job is None or current.principal != Principal.model_validate(raw_job["actor"]):
            raise inaccessible_reference()
        command_key = self.queue.transactions.key(current, job.kg_id, command.idempotency_key)
        previous_command = await self.database.publication_commands.find_one(command_key)
        if previous_command and previous_command["command_hash"] != command_hash:
            raise DomainError(
                ErrorCode.IDEMPOTENCY_CONFLICT, "Publication key has different input."
            )
        if attempt["state"] == "accepted":
            if (
                attempt.get("publish_key") != command.idempotency_key
                or attempt.get("publish_command_hash") != command_hash
            ):
                raise DomainError(
                    ErrorCode.IDEMPOTENCY_CONFLICT, "Publication key has different input."
                )
            return RevisionReceipt.model_validate(attempt["receipt"])
        verified = await self.verify(current, command.report.receipts[0])
        if verified != command.report:
            raise inaccessible_reference()
        document = await self._document(current, attempt)
        if not isinstance(document, EmbeddedDocument):
            raise inconsistent()
        if job.kind in ("restore_material", "reprocess_material"):
            from domain_kg.materials.restoration import verify_restoration_original

            permit = await restoration_permit(self.queue, current, job)
            if permit is None:
                raise inaccessible_reference()
            await verify_restoration_original(self.queue, current, permit)
        stores = await self.queue.router.for_kg(current, job.kg_id)
        ns = {"org_id": str(job.org_id), "kg_id": str(job.kg_id)}
        binding = AcceptedProjection(
            **source.model_dump(exclude={"material_id", "version_id"}),
            source=source,
            attempt_id=batch.attempt_id,
            manifest_id=batch.manifest_id,
            generation=batch.generation,
            content_hash=batch.content_hash,
            chunk_artifact_id=document.chunk_checkpoint.artifact_id,
            import_origin=document.import_origin,
        )

        async def commit(session: AsyncClientSession) -> RevisionReceipt:
            active = await self.queue.authorize(job, session)
            await self.queue.transactions.fence(active, active.action, session)
            await check_import_origin(
                self.database, source, document.import_origin, session=session
            )
            await self.queue.validate_publication(job, lease, session)
            kg = await self.database.kgs.find_one(
                {"org_id": ns["org_id"], "id": ns["kg_id"]}, session=session
            )
            if kg is None or (kg["published_revision"], kg["generation"]) != (
                batch.expected_revision,
                str(batch.generation),
            ):
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The accepted KG revision changed.")
            if job.kind in ("repair", "restore_material"):
                parent_id = attempt.get("repair_parent_manifest_id")
                parent = await self.database.projection_attempts.find_one(
                    ns
                    | {
                        "id": parent_id,
                        "state": "accepted",
                        "batch.content_hash": batch.content_hash,
                    },
                    session=session,
                )
                current_binding = await self.database.publication_bindings.find_one(
                    ns
                    | {
                        "view_manifest_id": kg.get("published_manifest_id"),
                        "owner_key": f"source:{source.material_id}",
                        "binding.manifest_id": parent_id,
                    },
                    session=session,
                )
                if (
                    parent is None
                    or current_binding is None
                    or parent["source"] != source.model_dump(mode="json")
                ):
                    raise DomainError(
                        ErrorCode.VERSION_CONFLICT, "The accepted repair parent changed."
                    )
            else:
                schema = await schema_for_kg(self.queue.policy, active, job.kg_id, session=session)
                selected = await self.database.kg_provider_settings.find_one(ns, session=session)
                if selected is None or (
                    selected["profile_id"],
                    selected["profile_revision"],
                    selected["revision"],
                    selected.get("embedding_space_id"),
                    schema.id,
                    schema.revision,
                ) != (
                    str(document.profile.id),
                    document.profile.revision,
                    document.profile_selection_revision,
                    document.profile.space_id,
                    document.schema_id,
                    document.schema_revision,
                ):
                    raise DomainError(
                        ErrorCode.VERSION_CONFLICT, "Projection configuration changed."
                    )
            await stores.authority.require_version(
                ScopedRef(org_id=source.org_id, kg_id=source.kg_id, id=source.version_id),
                session=session,
            )
            version = await stores.authority._database.version_blobs.find_one(
                ns | {"id": str(source.version_id), "material_id": str(source.material_id)},
                session=session,
            )
            material_key = ns | {"id": str(source.material_id)}
            material = await self.database.material_access.find_one(material_key, session=session)
            if version is None or material is None:
                raise inaccessible_reference()
            if (
                job.kind in ("ingest", "import", "reprocess_material")
                and material.get("active_version_id") is not None
            ):
                raise DomainError(
                    ErrorCode.VERSION_CONFLICT, "This source already has an accepted version."
                )
            previous = [
                row
                async for row in self.database.publication_bindings.find(
                    ns | {"view_manifest_id": kg.get("published_manifest_id")}, session=session
                )
            ]
            retained = [
                row for row in previous if row["owner_key"] != f"source:{source.material_id}"
            ]
            for row in retained:
                await self.database.publication_bindings.insert_one(
                    {key: value for key, value in row.items() if key != "_id"}
                    | {"view_manifest_id": str(batch.manifest_id)},
                    session=session,
                )
            await self.database.publication_bindings.insert_one(
                ns
                | {
                    "view_manifest_id": str(batch.manifest_id),
                    "owner_key": f"source:{source.material_id}",
                    "binding": binding.model_dump(mode="json"),
                },
                session=session,
            )
            result = RevisionReceipt(
                org_id=job.org_id,
                kg_id=job.kg_id,
                revision_id=batch.revision_id,
                state="published",
                published_revision=batch.expected_revision + 1,
            )
            await self.database.publication_manifests.insert_one(
                ns
                | {
                    "id": str(batch.manifest_id),
                    "revision": batch.expected_revision + 1,
                    "generation": str(batch.generation),
                    "state": "accepted",
                    "binding_count": len(retained) + 1,
                    "job_id": str(job.id),
                    "attempt_id": str(job.attempt_id),
                    "created_at": now(),
                },
                session=session,
            )
            proposed = version["proposed"]
            timestamp = datetime.now(UTC)
            metadata = MaterialMetadata(
                material_id=source.material_id,
                version_id=source.version_id,
                media_type=version["blob"]["media_type"],
                tags=tuple(proposed.get("tags", [])),
                author=proposed.get("author"),
                source=proposed.get("source"),
                language=proposed.get("language"),
                created_at=material.get("metadata", {}).get("created_at", timestamp),
                updated_at=timestamp,
            )
            if job.kind in ("repair", "restore_material"):
                metadata = MaterialMetadata.model_validate(material["metadata"])
            # ACLs and membership grants have their own owner-authorized policy service.
            material_update: dict[str, Any] = {
                "active_version_id": str(source.version_id),
                "metadata": metadata.model_dump(mode="json"),
            }
            if job.kind in ("restore_material", "reprocess_material"):
                material_update.update(
                    excluded=False, tombstoned=False, purge_after=None, restored_at=timestamp
                )
            await self.database.material_access.update_one(
                material_key,
                {
                    "$set": material_update,
                    "$inc": {"revision": 1},
                },
                session=session,
            )
            if job.kind not in ("repair", "restore_material"):
                await stores.authority._database.materials.update_one(
                    material_key, {"$set": {"title": proposed["title"]}}, session=session
                )
            if material.get("active_version_id") and material["active_version_id"] != str(
                source.version_id
            ):
                await stores.authority._database.version_blobs.update_one(
                    ns | {"id": material["active_version_id"]},
                    {"$set": {"state": "retained"}},
                    session=session,
                )
            await stores.authority._database.version_blobs.update_one(
                ns | {"id": str(source.version_id)},
                {"$set": {"state": "accepted"}},
                session=session,
            )
            if job.kind in ("restore_material", "reprocess_material"):
                from domain_kg.materials.models import RestorationReceipt

                restoration = RestorationReceipt(
                    org_id=job.org_id,
                    kg_id=job.kg_id,
                    material_id=source.material_id,
                    revision=material["revision"] + 1,
                    state="restored",
                    job_id=job.id,
                )
                changed_intent = await self.database.material_commands.update_one(
                    ns
                    | {
                        "id": str(job.restore_intent_id),
                        "operation": "restore",
                        "state": "pending",
                        "job_id": str(job.id),
                        "expected_revision": material["revision"],
                    },
                    {
                        "$set": {
                            "state": "accepted",
                            "accepted_at": timestamp,
                            "receipt": restoration.model_dump(mode="json"),
                        }
                    },
                    session=session,
                )
                if not changed_intent.matched_count:
                    raise DomainError(ErrorCode.VERSION_CONFLICT, "The restoration intent changed.")
            changed = await self.database.kgs.update_one(
                {
                    "org_id": ns["org_id"],
                    "id": ns["kg_id"],
                    "published_revision": batch.expected_revision,
                    "publication_fence": lease.fence,
                    "publication_lease.lease_token": str(lease.lease_token),
                },
                {
                    "$set": {
                        "published_manifest_id": str(batch.manifest_id),
                        "published_revision": batch.expected_revision + 1,
                        "publication_lease.lease_until": now(),
                    },
                    "$inc": {"visibility_revision": 1},
                },
                session=session,
            )
            if not changed.matched_count:
                raise DomainError(
                    ErrorCode.VERSION_CONFLICT, "Publication compare-and-swap failed."
                )
            changed = await self.database.jobs.update_one(
                self.queue._active(job),
                {
                    "$set": {
                        "stage": "ready_with_warnings" if document.warnings else "ready",
                        "warnings": list(document.warnings),
                        "progress": 1,
                        "accepted_manifest_id": str(batch.manifest_id),
                        "lease_until": now(),
                        "error_code": None,
                    }
                },
                session=session,
            )
            if not changed.matched_count:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "Worker compare-and-swap failed.")
            changed = await self.database.projection_attempts.update_one(
                ns | {"id": str(batch.manifest_id), "state": "verified"},
                {
                    "$set": {
                        "state": "accepted",
                        "publish_key": command.idempotency_key,
                        "publish_command_hash": command_hash,
                        "receipt": result.model_dump(mode="json"),
                    }
                },
                session=session,
            )
            if not changed.matched_count:
                raise DomainError(
                    ErrorCode.VERSION_CONFLICT, "The staged readiness marker changed."
                )
            await self.database.publication_commands.insert_one(
                command_key
                | {
                    "command_hash": command_hash,
                    "manifest_id": str(batch.manifest_id),
                    "receipt": result.model_dump(mode="json"),
                },
                session=session,
            )
            if job.kind == "repair":
                await self.database.repair_requests.update_many(
                    ns
                    | {
                        "manifest_id": attempt["repair_parent_manifest_id"],
                    },
                    {"$set": {"state": "resolved", "accepted_manifest_id": str(batch.manifest_id)}},
                    session=session,
                )
            await self.database.outbox.insert_one(
                ns
                | {
                    "id": str(batch.revision_id),
                    "kind": "revision_published",
                    "manifest_id": str(batch.manifest_id),
                    "delivered": False,
                    "created_at": now(),
                },
                session=session,
            )
            await self.database.policy_audit.insert_one(
                ns
                | {
                    "operation": "publish_revision",
                    "actor": active.principal.model_dump(mode="json"),
                    "manifest_id": str(batch.manifest_id),
                    "revision": result.published_revision,
                },
                session=session,
            )
            return result

        try:
            result = await self.queue.transactions.transaction(commit)
        except DomainError as error:
            if error.code != ErrorCode.VERSION_CONFLICT:
                raise
            latest = await self.journal.attempt(current, batch.manifest_id)
            if latest["state"] != "accepted" or latest.get("publish_command_hash") != command_hash:
                raise
            result = RevisionReceipt.model_validate(latest["receipt"])
        await self.queue.policy.revalidate(current)
        if not await self.queue.policy.material_visible(current, job.kg_id, source.material_id):
            raise inaccessible_reference()
        return result
