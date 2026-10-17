"""Reseal one selected source-artifact chain with explicit restore provenance."""

import hashlib
from dataclasses import dataclass, field
from typing import Literal, Self
from uuid import UUID

from pydantic import TypeAdapter, ValidationError, model_validator

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.imports.legacy_models import LegacyGraph
from domain_kg.imports.prepared import PreparedImport
from domain_kg.ingestion.models import ParsedDocument
from domain_kg.ingestion.resolution import fingerprint
from domain_kg.ingestion.resolved import ExtractionArtifact, ResolvedDocument
from domain_kg.jobs.models import Checkpoint, JobArtifact
from domain_kg.materials.models import BlobRef
from domain_kg.models import SHA256, FrozenModel, ScopedRef, SourceVersion
from domain_kg.operations.restore_documents import RestoreDocuments


class RestoreArtifactOrigin(FrozenModel):
    transform_version: Literal["typed-source-restore-v1"] = "typed-source-restore-v1"
    restore_operation: ScopedRef
    original: JobArtifact
    restored: JobArtifact
    dependency_hashes: tuple[SHA256, ...]

    @model_validator(mode="after")
    def exact_identity(self) -> Self:
        old, new = self.original, self.restored
        if (
            (old.org_id, old.kg_id) != (self.restore_operation.org_id, self.restore_operation.kg_id)
            or old.org_id != new.org_id
            or old.kg_id == new.kg_id
            or (old.id, old.job_id, old.source_version_id, old.stage, old.blob.material_id)
            != (new.id, new.job_id, new.source_version_id, new.stage, new.blob.material_id)
        ):
            raise ValueError("restored artifacts require explicit distinct-KG origin")
        return self


@dataclass(frozen=True)
class RewrittenArtifact:
    artifact: JobArtifact
    checkpoint: Checkpoint
    body: bytes = field(repr=False)
    origin: RestoreArtifactOrigin


class RestoreArtifactLedger:
    """No I/O or active leases. A caller selects and orders a sealed source chain.

    Metadata stays bounded; output bodies are returned for immediate streaming,
    rather than accumulated here. Alternative historical chains require their own
    selection. This helper cannot make a target accepted or approve its provider.
    """

    STAGES = ("validating", "parsing", "chunking", "extracting", "resolving")

    def __init__(self, documents: RestoreDocuments, operation: ScopedRef) -> None:
        if (operation.org_id, operation.kg_id) != (
            documents.mapping.origin.org_id,
            documents.mapping.origin.kg_id,
        ):
            raise inaccessible_reference()
        self.documents, self.operation = documents, operation
        self._checkpoints: dict[UUID, tuple[Checkpoint, Checkpoint]] = {}
        self._stages: dict[tuple[UUID, str], UUID] = {}
        self._sources: dict[UUID, SourceVersion] = {}
        self._canonical: dict[UUID, str] = {}

    def checkpoint(self, previous: Checkpoint) -> Checkpoint:
        pair = self._checkpoints.get(previous.artifact_id)
        if pair is None or previous != pair[0]:
            raise self.inconsistent()
        return pair[1]

    @staticmethod
    def inconsistent() -> DomainError:
        return DomainError(
            ErrorCode.PROJECTION_INCONSISTENT, "Restore artifact dependencies differ."
        )

    def rewrite(
        self, artifact: JobArtifact, checkpoint: Checkpoint, body: bytes
    ) -> RewrittenArtifact:
        try:
            return self._rewrite(
                JobArtifact.model_validate(artifact), Checkpoint.model_validate(checkpoint), body
            )
        except ValidationError:
            raise self.inconsistent() from None

    def _rewrite(
        self, artifact: JobArtifact, checkpoint: Checkpoint, body: bytes
    ) -> RewrittenArtifact:
        if (
            artifact.org_id,
            artifact.kg_id,
            artifact.id,
            artifact.job_id,
            artifact.stage,
            artifact.input_hash,
            artifact.blob.sha256,
        ) != (
            checkpoint.org_id,
            checkpoint.kg_id,
            checkpoint.artifact_id,
            checkpoint.job_id,
            checkpoint.stage,
            checkpoint.input_hash,
            checkpoint.output_hash,
        ):
            raise self.inconsistent()
        owner = self.documents.owner(artifact.blob)
        self.documents.mapping.source(owner)
        if artifact.source_version_id != owner.version_id or (
            len(body) != artifact.blob.size_bytes
            or hashlib.sha256(body).hexdigest() != checkpoint.output_hash
        ):
            raise self.inconsistent()
        if checkpoint.stage not in self.STAGES:
            raise DomainError(
                ErrorCode.UNSUPPORTED_FORMAT, "This restore artifact stage is unsupported."
            )
        if len(self._checkpoints) >= 10000 and artifact.id not in self._checkpoints:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Restore artifact inventory is too large.")
        if artifact.job_id in self._sources and self._sources[artifact.job_id] != owner:
            raise self.inconsistent()
        key = (artifact.job_id, artifact.stage)
        if key in self._stages and self._stages[key] != artifact.id:
            raise self.inconsistent()
        position = self.STAGES.index(checkpoint.stage)
        hashes: tuple[str, ...] = ()
        if position:
            dependency = self._stages.get((artifact.job_id, self.STAGES[position - 1]))
            if dependency is None:
                raise self.inconsistent()
            hashes = (self._checkpoints[dependency][1].output_hash,)
        canonical = ""
        rewritten: (
            BlobRef
            | ParsedDocument
            | ExtractionArtifact
            | ResolvedDocument
            | LegacyGraph
            | PreparedImport
        )
        if checkpoint.stage == "validating":
            original = BlobRef.model_validate_json(body)
            if self.documents.owner(original) != owner:
                raise self.inconsistent()
            rewritten = self.documents.original(original)
            hashes = (original.sha256,)
        elif checkpoint.stage in ("parsing", "chunking"):
            parsed = ParsedDocument.model_validate_json(body)
            if self.documents.owner(parsed.blob) != owner:
                raise self.inconsistent()
            rewritten = self.documents.parsed(parsed)
        elif checkpoint.stage == "extracting":
            extraction = TypeAdapter(ExtractionArtifact | LegacyGraph).validate_json(body)
            canonical = hashlib.sha256(extraction.model_dump_json().encode()).hexdigest()
            if isinstance(extraction, LegacyGraph):
                rewritten = self.documents.legacy(extraction, owner)
            else:
                if self.documents.mapping.source(extraction) != self.documents.mapping.source(
                    owner
                ):
                    raise self.inconsistent()
                if self._stages.get((artifact.job_id, "chunking")) != extraction.chunk_artifact_id:
                    raise self.inconsistent()
                rewritten = self.documents.extraction(extraction)
        else:
            resolved = TypeAdapter(ResolvedDocument | PreparedImport).validate_json(body)
            if self.documents.mapping.source(resolved) != self.documents.mapping.source(owner):
                raise self.inconsistent()
            previous = self._stages.get((artifact.job_id, "extracting"))
            graph_hash = (
                resolved.import_origin.graph_hash
                if isinstance(resolved, PreparedImport)
                else (hashlib.sha256(resolved.extraction.model_dump_json().encode()).hexdigest())
            )
            if previous is None or graph_hash != self._canonical[previous]:
                raise self.inconsistent()
            rewritten = (
                self.documents.prepared(resolved, self.checkpoint)
                if isinstance(resolved, PreparedImport)
                else self.documents.resolved(resolved)
            )
        data = rewritten.model_dump_json().encode()
        if len(data) > 50 * 1024 * 1024:
            raise DomainError(
                ErrorCode.QUOTA_EXCEEDED, "Restored artifact exceeds its byte budget."
            )
        input_hash = fingerprint(
            {
                "transform": "typed-source-restore-v1",
                "origin": checkpoint.model_dump(mode="json"),
                "operation": self.operation.model_dump(mode="json"),
                "dependencies": hashes,
            }
        )
        blob = BlobRef.model_validate(
            artifact.blob.model_dump()
            | self.documents.mapping.target.model_dump()
            | {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
        )
        result = JobArtifact.model_validate(
            artifact.model_dump()
            | self.documents.mapping.target.model_dump()
            | {"blob": blob, "input_hash": input_hash}
        )
        new_checkpoint = Checkpoint.model_validate(
            checkpoint.model_dump()
            | self.documents.mapping.target.model_dump()
            | {"input_hash": input_hash, "output_hash": blob.sha256}
        )
        existing = self._checkpoints.get(artifact.id)
        if existing is not None and existing != (checkpoint, new_checkpoint):
            raise self.inconsistent()
        self._checkpoints[artifact.id] = (checkpoint, new_checkpoint)
        self._stages[key] = artifact.id
        self._sources[artifact.job_id] = owner
        self._canonical[artifact.id] = canonical
        return RewrittenArtifact(
            result,
            new_checkpoint,
            data,
            RestoreArtifactOrigin(
                restore_operation=self.operation,
                original=artifact,
                restored=result,
                dependency_hashes=hashes,
            ),
        )
