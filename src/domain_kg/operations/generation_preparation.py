"""Read one sealed artifact at a time under the exact live rebuild permit."""

import hashlib

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.jobs.models import Checkpoint, LeasedJob
from domain_kg.jobs.operations import OperationArtifact
from domain_kg.jobs.queue import JobQueue
from domain_kg.operations.generation_capture import GenerationCapture
from domain_kg.operations.generation_targets import GenerationInput, invalid
from domain_kg.revisions.curation_publication import directive
from domain_kg.revisions.journal import PublicationJournal, read_artifact
from domain_kg.revisions.models import ReadinessReport, RevisionBatch
from domain_kg.revisions.projections import (
    CurationEmbeddedDocument,
    EmbeddedDocument,
    ProjectionDocument,
    digest,
)


class GenerationPreparation:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database = queue, queue.database
        self.capture = GenerationCapture(queue)

    async def document(
        self, job: LeasedJob, item: GenerationInput
    ) -> tuple[ProjectionDocument, Checkpoint]:
        access, _, target = await self.capture.guard(job)
        await self.capture.check_input(target, item)
        attempt = await PublicationJournal(self.queue).attempt(access, item.base.manifest_id)
        batch = RevisionBatch.model_validate(attempt["batch"])
        if attempt["state"] != "accepted" or any(
            getattr(batch, key) != getattr(item.base, key)
            for key in (
                "org_id",
                "kg_id",
                "manifest_id",
                "attempt_id",
                "generation",
                "content_hash",
            )
        ):
            raise invalid()
        ReadinessReport.model_validate(
            {"batch": attempt["batch"], "receipts": attempt["receipts"], "ready": True}
        )
        checkpoint = Checkpoint.model_validate(attempt["embedded_checkpoint"])
        document: ProjectionDocument
        ns = {"org_id": str(job.org_id), "kg_id": str(job.kg_id)}
        if item.base.source:
            from domain_kg.imports.provenance import check_import_origin
            from domain_kg.revisions.publication import source_of

            permit = await self.capture.source_permit(job, item.base.source)
            body = await read_artifact(
                self.queue, access, checkpoint, item.base.source, generation=permit
            )
            document = EmbeddedDocument.model_validate_json(body)
            if (
                source_of(document) != item.base.source
                or document.import_origin != item.base.import_origin
                or document.chunk_checkpoint.artifact_id != item.base.chunk_artifact_id
            ):
                raise invalid()
            await check_import_origin(self.database, item.base.source, document.import_origin)
        else:
            owner = item.base.curation
            if owner is None:
                raise invalid()
            record = await self.database.curation_revisions.find_one(
                ns | {"id": str(owner.id), "state": "accepted"}
            )
            mirror = await self.database.curation_access.find_one(ns | {"id": str(owner.id)})
            artifact = await self.database.operation_artifacts.find_one(
                ns
                | {
                    "id": str(checkpoint.artifact_id),
                    "owner": owner.model_dump(mode="json"),
                    "job_id": str(checkpoint.job_id),
                    "stage": "embedding",
                    "input_hash": checkpoint.input_hash,
                }
            )
            historical = await self.database.jobs.find_one(
                ns | {"id": str(checkpoint.job_id), "kind": "curate", "target_id": str(owner.id)}
            )
            if record is None or mirror is None or artifact is None or historical is None:
                raise invalid()
            typed = OperationArtifact.model_validate(
                {key: artifact[key] for key in OperationArtifact.model_fields}
            )
            body = typed.body.encode()
            document = CurationEmbeddedDocument.model_validate_json(body)
            if (
                document.curation != owner
                or document.directive != directive(record)
                or digest(document.directive.model_dump(mode="json")) != item.curation_hash
                or typed.output_hash != checkpoint.output_hash
                or record["job_id"] != str(checkpoint.job_id)
                or record["actor"] != historical["actor"]
                or any(mirror.get(key) != record.get(key) for key in ("public", "material_ids"))
            ):
                raise invalid()
        if (
            hashlib.sha256(body).hexdigest() != item.base.content_hash
            or checkpoint.output_hash != item.base.content_hash
        ):
            raise invalid()
        selected = await self.database.kg_provider_settings.find_one(ns)
        if selected is None or (
            selected["profile_id"],
            selected["profile_revision"],
            selected["embedding_space_id"],
            selected["embedding_model"],
            selected["embedding_dimensions"],
            selected["revision"],
        ) != (
            str(document.profile.id),
            document.profile.revision,
            document.profile.space_id,
            document.profile.model,
            document.profile.dimensions,
            document.profile_selection_revision,
        ):
            raise DomainError(
                ErrorCode.VERSION_CONFLICT,
                "Rebuild requires the current sealed embedding configuration.",
            )
        await self.capture.check_input(target, item)
        await self.capture.guard(job)
        return document, checkpoint
