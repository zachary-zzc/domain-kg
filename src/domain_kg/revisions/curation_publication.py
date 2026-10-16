"""Manual fields use immutable attempts and the same fenced acceptance point."""

from datetime import UTC
from typing import Any
from uuid import UUID, uuid5

from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.curation import authorize_curation
from domain_kg.graph.curation_models import ManualNode, NodeFields
from domain_kg.graph.models import DomainSchema
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.models import Checkpoint, LeasedJob, PublicationLease
from domain_kg.jobs.operations import read_operation_artifact
from domain_kg.jobs.queue import JobQueue, now
from domain_kg.models import ManualProvenance, Namespace, ScopedRef
from domain_kg.providers.embeddings import EmbeddingProfile
from domain_kg.providers.service import ProviderService
from domain_kg.revisions.journal import PublicationJournal
from domain_kg.revisions.models import PublishRevision, RevisionBatch, RevisionReceipt
from domain_kg.revisions.projections import (
    AcceptedProjection,
    CurationEmbeddedDocument,
    ProjectionBody,
    ProjectionRow,
    digest,
)
from domain_kg.storage.neo4j import inconsistent
from domain_kg.storage.router import projection_checksum


def directive(record: dict[str, Any]) -> ManualNode:
    ns = {"org_id": record["org_id"], "kg_id": record["kg_id"]}
    return ManualNode(
        curation=ScopedRef(**ns, id=record["id"]),
        entity=ScopedRef(**ns, id=record["target_id"]),
        changes=NodeFields.model_validate(record["after"]),
        provenance=ManualProvenance.model_validate(record["provenance"]),
        revision=record["expected_revision"] + 1,
        created_at=record["created_at"].replace(tzinfo=UTC),
        public=record["public"],
        material_ids=tuple(UUID(value) for value in record["material_ids"]),
    )


async def curation_document(
    queue: JobQueue, access: AccessContext, attempt: dict[str, Any], *, leased: bool = False
) -> CurationEmbeddedDocument:
    owner = ScopedRef.model_validate(attempt["curation"])
    checkpoint = Checkpoint.model_validate(attempt["embedded_checkpoint"])
    job = LeasedJob.model_validate(attempt["job"])
    if job.kind != "curate" or job.target_id != owner.id:
        raise inconsistent()
    data = await read_operation_artifact(
        queue, access, owner, checkpoint, job=job if leased else None
    )
    if digest_bytes(data) != attempt["batch"]["content_hash"]:
        raise inconsistent()
    document = CurationEmbeddedDocument.model_validate_json(data)
    record = await authorize_curation(queue, access, owner.id, kg_id=owner.kg_id)
    if document.curation != owner or document.directive != directive(record):
        raise inconsistent()
    return document


def digest_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


class CurationCoordinator:
    def __init__(self, queue: JobQueue, providers: ProviderService) -> None:
        self.queue, self.providers, self.database = queue, providers, queue.database
        self.journal = PublicationJournal(queue)

    async def input_hash(self, job: LeasedJob, stage: str) -> str:
        access = await self.queue.authorize(job)
        record = await authorize_curation(
            self.queue, access, job.target_id, kg_id=job.kg_id, write=True, preparing=True
        )
        selected = await self.providers.selected(access, job.kg_id)
        return digest(
            {
                "operation": "manual-node-v1",
                "curation": record["id"],
                "after": record["after"],
                "schema": record["domain_schema"],
                "selection": {key: value for key, value in selected.items() if key != "_id"},
                "stage": stage,
                "attempt": str(job.attempt_id) if stage != "embedding" else None,
            }
        )

    async def embed(self, job: LeasedJob, input_hash: str) -> Checkpoint:
        access = await self.queue.authorize(job)
        record = await authorize_curation(
            self.queue, access, job.target_id, kg_id=job.kg_id, write=True, preparing=True
        )
        manual = directive(record)
        ns = {"org_id": str(job.org_id), "kg_id": str(job.kg_id)}
        selected = await self.providers.selected(access, job.kg_id)
        expected = EmbeddingProfile(
            org_id=job.org_id,
            kg_id=job.kg_id,
            id=selected["profile_id"],
            revision=selected["profile_revision"],
            model=selected["embedding_model"],
            dimensions=selected["embedding_dimensions"],
            space_id=selected["embedding_space_id"],
        )
        vector = None
        if manual.text:
            kg = await self.database.kgs.find_one({"org_id": ns["org_id"], "id": ns["kg_id"]})
            previous = await self.database.publication_bindings.find_one(
                ns
                | {
                    "view_manifest_id": kg.get("published_manifest_id") if kg else None,
                    "owner_key": self.owner_key(record),
                }
            )
            if previous:
                from domain_kg.revisions.publication import accepted_document

                old, _, _ = await accepted_document(
                    self.queue, access, AcceptedProjection.model_validate(previous["binding"])
                )
                if (
                    isinstance(old, CurationEmbeddedDocument)
                    and old.profile == expected
                    and (old.directive.text == manual.text)
                    and old.rows
                ):
                    vector = old.rows[0].vector
            if vector is None:
                profile, vectors = await self.providers.embeddings(
                    access,
                    Namespace(org_id=job.org_id, kg_id=job.kg_id),
                    [manual.text],
                    curation=job,
                )
                if profile != expected or len(vectors) != 1:
                    raise DomainError(
                        ErrorCode.VERSION_CONFLICT, "The manual embedding profile changed."
                    )
                vector = tuple(vectors[0])
        schema = DomainSchema.model_validate(record["domain_schema"])
        body = (
            ProjectionBody(
                org_id=job.org_id,
                kg_id=job.kg_id,
                id=job.target_id,
                curation=manual.curation,
                kind="curation",
                manual_text=manual.text,
                provenance=manual.provenance,
                manual_node=manual,
            )
            if manual.text
            else None
        )
        document = CurationEmbeddedDocument(
            org_id=job.org_id,
            kg_id=job.kg_id,
            curation=manual.curation,
            directive=manual,
            profile=expected,
            profile_selection_revision=selected["revision"],
            schema_id=schema.id,
            schema_revision=schema.revision,
            rows=(ProjectionRow(**body.model_dump(), vector=vector),)
            if body is not None and vector is not None
            else (),
        )
        if await self.input_hash(job, "embedding") != input_hash:
            raise DomainError(ErrorCode.VERSION_CONFLICT, "The manual embedding input changed.")
        return await self.queue.store_artifact(
            job, "embedding", input_hash, document.model_dump_json().encode()
        )

    @staticmethod
    def owner_key(record: dict[str, Any]) -> str:
        return f"curation:{record['target_id']}:{record['scope_key']}"

    async def prepare(self, job: LeasedJob, checkpoint: Checkpoint) -> RevisionBatch:
        await self.journal.initialize()
        access = await self.queue.authorize(job)
        record = await authorize_curation(
            self.queue, access, job.target_id, kg_id=job.kg_id, write=True, preparing=True
        )
        owner = ScopedRef(org_id=job.org_id, kg_id=job.kg_id, id=job.target_id)
        data = await read_operation_artifact(self.queue, access, owner, checkpoint, job=job)
        document = CurationEmbeddedDocument.model_validate_json(data)
        if checkpoint.stage != "embedding" or document.directive != directive(record):
            raise inconsistent()
        lease = await self.queue.acquire_publication(job)
        kg = await self.database.kgs.find_one({"org_id": str(job.org_id), "id": str(job.kg_id)})
        if kg is None:
            raise inaccessible_reference()
        batch = RevisionBatch(
            org_id=job.org_id,
            kg_id=job.kg_id,
            revision_id=job.target_id,
            attempt_id=job.attempt_id,
            generation=kg["generation"],
            manifest_id=uuid5(job.attempt_id, "manifest"),
            content_hash=checkpoint.output_hash,
            expected_revision=kg["published_revision"],
            records=tuple(
                ScopedRef(org_id=job.org_id, kg_id=job.kg_id, id=row.id) for row in document.rows
            ),
        )
        value = {
            "org_id": str(job.org_id),
            "kg_id": str(job.kg_id),
            "id": str(batch.manifest_id),
            "job": job.model_dump(mode="json"),
            "lease": lease.model_dump(mode="json"),
            "curation": owner.model_dump(mode="json"),
            "embedded_checkpoint": checkpoint.model_dump(mode="json"),
            "batch": batch.model_dump(mode="json") | {"records": []},
            "record_count": len(batch.records),
            "record_checksum": projection_checksum([str(ref.id) for ref in batch.records]),
            "state": "prepared",
            "receipts": [],
        }

        async def save(session: AsyncClientSession) -> None:
            await self.queue.validate_publication(job, lease, session)
            existing = await self.database.projection_attempts.find_one(
                {"org_id": value["org_id"], "kg_id": value["kg_id"], "id": value["id"]},
                session=session,
            )
            if existing:
                if (
                    existing["batch"] != value["batch"]
                    or existing["embedded_checkpoint"] != value["embedded_checkpoint"]
                ):
                    raise DomainError(ErrorCode.VERSION_CONFLICT, "The manual attempt changed.")
                return
            await self.database.projection_attempts.insert_one(value, session=session)
            for position, ref in enumerate(batch.records):
                await self.database.projection_record_refs.insert_one(
                    {
                        "org_id": value["org_id"],
                        "kg_id": value["kg_id"],
                        "manifest_id": value["id"],
                        "position": position,
                        "reference": ref.model_dump(mode="json"),
                    },
                    session=session,
                )
            changed = await self.database.jobs.update_one(
                self.queue._active(job),
                {"$set": {"pending_manifest_id": str(batch.manifest_id)}},
                session=session,
            )
            if not changed.matched_count:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The manual worker lease changed.")

        await self.queue.transactions.transaction(save)
        return batch

    async def publish(self, access: AccessContext, command: PublishRevision) -> RevisionReceipt:
        command = PublishRevision.model_validate(command)
        current = await self.queue.policy.revalidate(access, action=Action.CURATE)
        try:
            return await self._publish(current, command)
        except DomainError as error:
            if error.code != ErrorCode.VERSION_CONFLICT:
                raise
            latest = await self.journal.attempt(current, command.report.batch.manifest_id)
            if latest["state"] != "accepted" or latest.get("publish_command_hash") != (
                digest_bytes(command.model_dump_json().encode())
            ):
                raise
            job = LeasedJob.model_validate(latest["job"])
            await authorize_curation(
                self.queue, current, job.target_id, kg_id=job.kg_id, write=True
            )
            return RevisionReceipt.model_validate(latest["receipt"])

    async def _publish(self, access: AccessContext, command: PublishRevision) -> RevisionReceipt:
        from domain_kg.revisions.publication import ProjectionCoordinator

        batch = command.report.batch
        attempt = await self.journal.attempt(access, batch.manifest_id)
        job, lease = (
            LeasedJob.model_validate(attempt["job"]),
            PublicationLease.model_validate(attempt["lease"]),
        )
        record = await authorize_curation(
            self.queue, access, job.target_id, kg_id=job.kg_id, write=True
        )
        if RevisionBatch.model_validate(attempt["batch"]) != batch or job.kind != "curate":
            raise inaccessible_reference()
        command_hash = digest_bytes(command.model_dump_json().encode())
        key = self.queue.transactions.key(access, job.kg_id, command.idempotency_key)
        if attempt["state"] == "accepted":
            if attempt.get("publish_command_hash") != command_hash:
                raise DomainError(
                    ErrorCode.IDEMPOTENCY_CONFLICT, "Publication key has different input."
                )
            return RevisionReceipt.model_validate(attempt["receipt"])
        if (command.lease_token, command.fence) != (lease.lease_token, lease.fence):
            raise DomainError(ErrorCode.VERSION_CONFLICT, "The manual publication lease changed.")
        if command.expected_target_revisions and command.expected_target_revisions != (
            (UUID(record["target_id"]), record["expected_revision"]),
        ):
            raise inaccessible_reference()
        coordinator = ProjectionCoordinator(self.queue, self.providers)
        if await coordinator.verify(access, command.report.receipts[0]) != command.report:
            raise inconsistent()
        document = await curation_document(self.queue, access, attempt, leased=True)
        binding = AcceptedProjection(
            org_id=job.org_id,
            kg_id=job.kg_id,
            curation=document.curation,
            attempt_id=job.attempt_id,
            manifest_id=batch.manifest_id,
            content_hash=batch.content_hash,
            generation=batch.generation,
        )
        ns = {"org_id": str(job.org_id), "kg_id": str(job.kg_id)}

        async def accept(session: AsyncClientSession) -> RevisionReceipt:
            actor = await self.queue.authorize(job, session)
            await self.queue.transactions.fence(actor, Action.CURATE, session)
            await self.queue.validate_publication(job, lease, session)
            current = await authorize_curation(
                self.queue,
                actor,
                job.target_id,
                kg_id=job.kg_id,
                write=True,
                preparing=True,
                session=session,
            )
            kg = await self.database.kgs.find_one(
                {"org_id": ns["org_id"], "id": ns["kg_id"]}, session=session
            )
            selected = await self.database.kg_provider_settings.find_one(ns, session=session)
            if kg is None or (kg["published_revision"], kg["generation"]) != (
                batch.expected_revision,
                str(batch.generation),
            ):
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The accepted KG view changed.")
            if selected is None or (
                selected["profile_id"],
                selected["profile_revision"],
                selected["revision"],
                selected["embedding_space_id"],
            ) != (
                str(document.profile.id),
                document.profile.revision,
                document.profile_selection_revision,
                document.profile.space_id,
            ):
                raise DomainError(
                    ErrorCode.VERSION_CONFLICT, "Manual projection configuration changed."
                )
            previous_command = await self.database.publication_commands.find_one(
                key, session=session
            )
            if previous_command:
                raise DomainError(
                    ErrorCode.IDEMPOTENCY_CONFLICT, "Publication key has different input."
                )
            prior = [
                row
                async for row in self.database.publication_bindings.find(
                    ns | {"view_manifest_id": kg.get("published_manifest_id")}, session=session
                )
            ]
            retained = [row for row in prior if row["owner_key"] != self.owner_key(current)]
            for row in retained:
                await self.database.publication_bindings.insert_one(
                    {field: value for field, value in row.items() if field != "_id"}
                    | {"view_manifest_id": str(batch.manifest_id)},
                    session=session,
                )
            await self.database.publication_bindings.insert_one(
                ns
                | {
                    "view_manifest_id": str(batch.manifest_id),
                    "owner_key": self.owner_key(current),
                    "binding": binding.model_dump(mode="json"),
                },
                session=session,
            )
            changed = await self.database.curation_targets.update_one(
                ns
                | {
                    "id": current["target_state_id"],
                    "revision": current["expected_revision"],
                    "target_id": current["target_id"],
                    "scope_key": current["scope_key"],
                },
                {"$inc": {"revision": 1}},
                session=session,
            )
            if not changed.matched_count:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The curation target changed.")
            await self.database.curation_scopes.update_one(
                ns | {"target_id": current["target_id"], "scope_key": current["scope_key"]},
                {"$set": {"curation_id": current["id"], "changes": current["after"]}},
                upsert=True,
                session=session,
            )
            receipt = RevisionReceipt(
                org_id=job.org_id,
                kg_id=job.kg_id,
                revision_id=job.target_id,
                state="published",
                published_revision=batch.expected_revision + 1,
                job_id=job.id,
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
            changed = await self.database.kgs.update_one(
                {
                    "org_id": ns["org_id"],
                    "id": ns["kg_id"],
                    "published_revision": batch.expected_revision,
                    "publication_lease.lease_token": str(lease.lease_token),
                    "publication_fence": lease.fence,
                },
                {
                    "$set": {
                        "published_manifest_id": str(batch.manifest_id),
                        "publication_lease.lease_until": now(),
                    },
                    "$inc": {"published_revision": 1, "visibility_revision": 1},
                },
                session=session,
            )
            if not changed.matched_count:
                raise DomainError(
                    ErrorCode.VERSION_CONFLICT, "Manual publication compare-and-swap failed."
                )
            changed = await self.database.jobs.update_one(
                self.queue._active(job),
                {
                    "$set": {
                        "stage": "ready",
                        "progress": 1,
                        "accepted_manifest_id": str(batch.manifest_id),
                        "lease_until": now(),
                        "error_code": None,
                    }
                },
                session=session,
            )
            if not changed.matched_count:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The manual worker lease changed.")
            await self.database.curation_revisions.update_one(
                ns | {"id": current["id"], "state": "pending"},
                {
                    "$set": {
                        "state": "accepted",
                        "accepted_manifest_id": str(batch.manifest_id),
                        "receipt": receipt.model_dump(mode="json"),
                    }
                },
                session=session,
            )
            changed = await self.database.projection_attempts.update_one(
                ns | {"id": str(batch.manifest_id), "state": "verified"},
                {
                    "$set": {
                        "state": "accepted",
                        "publish_key": command.idempotency_key,
                        "publish_command_hash": command_hash,
                        "receipt": receipt.model_dump(mode="json"),
                    }
                },
                session=session,
            )
            if not changed.matched_count:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "Manual readiness changed.")
            await self.database.publication_commands.insert_one(
                key
                | {
                    "command_hash": command_hash,
                    "manifest_id": str(batch.manifest_id),
                    "receipt": receipt.model_dump(mode="json"),
                },
                session=session,
            )
            for collection, kind in (
                ("audit", "curation_published"),
                ("outbox", "revision_published"),
            ):
                await self.database[collection].insert_one(
                    ns
                    | {
                        "id": str(uuid5(job.target_id, collection)),
                        "kind": kind,
                        "revision_id": str(job.target_id),
                        "actor": actor.principal.model_dump(mode="json"),
                        "created_at": now(),
                        "delivered": False,
                    },
                    session=session,
                )
            return receipt

        return await self.queue.transactions.transaction(accept)
