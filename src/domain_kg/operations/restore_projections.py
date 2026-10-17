"""Immutable target projections have read-back receipts, without making a KG active."""

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Self
from uuid import UUID, uuid5

from pydantic import model_validator

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.jobs.models import Checkpoint, JobArtifact, JobCommand, LeasedJob
from domain_kg.jobs.queue import JobQueue
from domain_kg.materials.models import BlobRef
from domain_kg.models import SHA256, Namespace, ScopedRef, SourceVersion
from domain_kg.operations.restore_preparation import PreparedRestore, RestorePreparation
from domain_kg.operations.restore_stores import RestoreStores
from domain_kg.providers.service import ProviderService
from domain_kg.revisions.models import ReadinessReport, RevisionBatch
from domain_kg.revisions.projections import AcceptedProjection, digest
from domain_kg.revisions.publication import projection_rows, verify_document
from domain_kg.storage.blobs import BlobStore
from domain_kg.storage.neo4j import GraphProjection
from domain_kg.storage.qdrant import VectorProjection


class RestoredProjectionOrigin(Namespace):
    operation_id: UUID
    job_id: UUID
    source_kg_id: UUID
    source_backup_id: UUID
    source_header_sha: SHA256
    attempt_id: UUID
    root_manifest_id: UUID
    manifest_id: UUID
    projection_attempt_id: UUID
    generation: UUID
    content_hash: SHA256
    source: SourceVersion

    @model_validator(mode="after")
    def distinct_scoped_origin(self) -> Self:
        if self.source_kg_id == self.kg_id or (self.source.org_id, self.source.kg_id) != (
            self.org_id,
            self.kg_id,
        ):
            raise ValueError(
                "restored projection requires a distinct source KG and exact target owner"
            )
        return self


@dataclass(frozen=True)
class RestoredProjection:
    binding: AcceptedProjection
    report: ReadinessReport
    checkpoint: Checkpoint
    artifact: JobArtifact
    job: JobCommand
    origin: RestoredProjectionOrigin


class RestoreProjections:
    def __init__(self, queue: JobQueue, providers: ProviderService) -> None:
        self.queue, self.providers = queue, providers
        self.bindings = RestoreStores(queue, providers.registry)
        self.preparation = RestorePreparation(queue, providers)

    async def stage(
        self, job: LeasedJob, staged: PreparedRestore
    ) -> tuple[RestoredProjection, ...]:
        if staged.curations:
            raise DomainError(
                ErrorCode.UNSUPPORTED_FORMAT,
                "Manual projection history requires its dedicated restore stage.",
            )
        if await self.bindings.approved(job) != (staged.binding.target, staged.binding.embedding):
            raise DomainError(ErrorCode.VERSION_CONFLICT, "Restore configuration changed.")
        inventory, binding = staged.inventory, staged.binding
        snapshots = {row["id"]: row for row in inventory.rows("projection_attempts")}
        artifacts = {row["id"]: row for row in inventory.rows("job_artifacts")}
        result = []
        ns = binding.target.target.model_dump()
        root_manifest_id = uuid5(job.attempt_id, "restore-root")
        for item in staged.sources:
            document = item.document
            source = SourceVersion(
                **{key: getattr(document, key) for key in SourceVersion.model_fields}
            )
            if (source.org_id, source.kg_id) != (
                binding.target.target.org_id,
                binding.target.target.kg_id,
            ):
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Restored projection source differs."
                )
            original = next(
                (
                    part
                    for part in inventory.root.projections
                    if part.source
                    and (part.source.material_id, part.source.version_id)
                    == (source.material_id, source.version_id)
                ),
                None,
            )
            if original is None:
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Original projection is missing."
                )
            attempt = snapshots.get(str(original.manifest_id))
            if attempt is None:
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Original projection attempt is missing."
                )
            old = Checkpoint.model_validate(attempt["embedded_checkpoint"])
            old_job = JobCommand.model_validate(
                {
                    key: attempt["job"][key]
                    for key in JobCommand.model_fields
                    if key in attempt["job"]
                }
            )
            raw = artifacts.get(str(old.artifact_id))
            if raw is None:
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Original embedding artifact is missing."
                )
            origin_artifact = JobArtifact.model_validate(
                {key: raw[key] for key in JobArtifact.model_fields}
            )
            origin_ns = (inventory.manifest.org_id, inventory.manifest.kg_id)
            if (
                (old.org_id, old.kg_id) != origin_ns
                or (old_job.org_id, old_job.kg_id) != origin_ns
                or old_job.target_id != source.version_id
                or old.stage != "embedding"
                or (origin_artifact.org_id, origin_artifact.kg_id) != origin_ns
                or origin_artifact.source_version_id != source.version_id
                or origin_artifact.blob.material_id != source.material_id
                or origin_artifact.id != old.artifact_id
                or origin_artifact.stage != old.stage
                or origin_artifact.input_hash != old.input_hash
                or old.job_id != old_job.id
                or origin_artifact.job_id != old.job_id
                or origin_artifact.blob.sha256 != old.output_hash
                or original.content_hash != old.output_hash
            ):
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Original embedding ownership differs."
                )
            data = document.model_dump_json().encode()
            checksum = hashlib.sha256(data).hexdigest()
            input_hash = digest(
                {
                    "restore_operation": str(job.target_id),
                    "origin": old.model_dump(mode="json"),
                    "profile": binding.embedding.model_dump(mode="json"),
                    "dependencies": [
                        document.chunk_checkpoint.output_hash,
                        document.resolved_checkpoint.output_hash,
                    ],
                }
            )
            blob = BlobRef(
                **source.model_dump(),
                object_id=uuid5(job.attempt_id, f"embedding/{old.artifact_id}/{checksum}"),
                sha256=checksum,
                size_bytes=len(data),
                media_type="application/json",
            )

            async def stream(data: bytes = data) -> AsyncIterator[bytes]:
                for start in range(0, len(data), 65536):
                    yield data[start : start + 65536]

            await self.preparation._put(job, binding, blob, stream())
            async for _ in BlobStore(binding.stores.blobs).open(blob):
                await self.preparation.targets.guard(job)
            artifact = JobArtifact(
                **ns,
                id=old.artifact_id,
                job_id=old.job_id,
                source_version_id=source.version_id,
                stage="embedding",
                input_hash=input_hash,
                blob=blob,
            )
            checkpoint = Checkpoint(
                **ns,
                job_id=old.job_id,
                stage="embedding",
                artifact_id=artifact.id,
                input_hash=input_hash,
                output_hash=checksum,
            )
            identifier = uuid5(job.attempt_id, f"source/{source.material_id}/{source.version_id}")
            batch = RevisionBatch(
                **ns,
                revision_id=uuid5(identifier, "revision"),
                attempt_id=identifier,
                generation=binding.target.generation,
                manifest_id=uuid5(identifier, "manifest"),
                content_hash=checksum,
                expected_revision=0,
                records=tuple(ScopedRef(**ns, id=row.id) for row in document.rows),
            )
            rows = projection_rows(document, batch)
            await GraphProjection(binding.stores.graph).write(rows)
            await self.preparation.targets.guard(job)
            await VectorProjection(binding.stores.vectors).write(
                rows, [list(row.vector) for row in document.rows]
            )
            receipts = await verify_document(binding.stores, document, batch)
            report = ReadinessReport(batch=batch, receipts=receipts, ready=True)
            accepted = AcceptedProjection(
                **ns,
                source=source,
                attempt_id=batch.attempt_id,
                generation=batch.generation,
                manifest_id=batch.manifest_id,
                content_hash=checksum,
                chunk_artifact_id=document.chunk_checkpoint.artifact_id,
                import_origin=document.import_origin,
            )
            history = JobCommand.model_validate(old_job.model_dump() | ns)
            provenance = RestoredProjectionOrigin(
                **ns,
                operation_id=job.target_id,
                job_id=job.id,
                source_kg_id=job.kg_id,
                source_backup_id=inventory.manifest.backup_id,
                source_header_sha=hashlib.sha256(
                    inventory.manifest.model_dump_json().encode()
                ).hexdigest(),
                attempt_id=job.attempt_id,
                root_manifest_id=root_manifest_id,
                manifest_id=batch.manifest_id,
                projection_attempt_id=batch.attempt_id,
                generation=batch.generation,
                content_hash=checksum,
                source=source,
            )
            result.append(
                RestoredProjection(accepted, report, checkpoint, artifact, history, provenance)
            )
        if await self.bindings.approved(job) != (binding.target, binding.embedding):
            raise DomainError(ErrorCode.VERSION_CONFLICT, "Restore configuration changed.")
        return tuple(result)
