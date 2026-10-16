"""Durable work receipts; request cancellation does not cancel accepted work."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from domain_kg.materials.models import BlobRef
from domain_kg.models import SHA256, IdempotencyKey, Namespace, NonNegative, UTCDateTime

JobStage = Literal[
    "queued",
    "validating",
    "parsing",
    "chunking",
    "extracting",
    "resolving",
    "embedding",
    "projecting",
    "verifying",
    "publishing",
    "ready",
    "ready_with_warnings",
    "failed",
    "cancelled",
    "repair_required",
]


class JobReceipt(Namespace):
    job_id: UUID
    stage: JobStage = "queued"
    progress: Annotated[float, Field(ge=0, le=1)] = 0
    repair_job_id: UUID | None = None
    warnings: tuple[str, ...] = ()


class JobCommand(Namespace):
    id: UUID
    kind: Literal[
        "ingest",
        "replace",
        "remove",
        "project",
        "repair",
        "import",
        "export",
        "backup",
        "restore",
        "restore_material",
        "purge",
    ]
    target_id: UUID
    idempotency_key: IdempotencyKey
    restore_intent_id: UUID | None = None

    @model_validator(mode="after")
    def restoration_intent(self) -> "JobCommand":
        if (self.kind == "restore_material") != (self.restore_intent_id is not None):
            raise ValueError("material restoration requires its exact durable intent")
        return self


class LeasedJob(JobCommand):
    lease_token: UUID
    fence: NonNegative
    lease_until: UTCDateTime
    attempt_id: UUID


PreparationStage = Literal[
    "validating",
    "parsing",
    "chunking",
    "extracting",
    "resolving",
    "embedding",
    "projecting",
    "verifying",
    "publishing",
]
ATTEMPT_STAGES = frozenset({"projecting", "verifying", "publishing"})


class Checkpoint(Namespace):
    job_id: UUID
    stage: PreparationStage
    artifact_id: UUID
    input_hash: SHA256
    output_hash: SHA256
    attempt_id: UUID | None = None

    @model_validator(mode="after")
    def projection_attempt(self) -> "Checkpoint":
        if (self.stage in ATTEMPT_STAGES) != (self.attempt_id is not None):
            raise ValueError("projection checkpoints require their exact attempt")
        return self


class PublicationLease(Namespace):
    job_id: UUID
    job_lease_token: UUID
    attempt_id: UUID
    lease_token: UUID
    fence: NonNegative
    lease_until: UTCDateTime


class JobArtifact(Namespace):
    id: UUID
    job_id: UUID
    source_version_id: UUID
    stage: PreparationStage
    input_hash: SHA256
    blob: BlobRef

    @model_validator(mode="after")
    def source_scope(self) -> "JobArtifact":
        if (self.org_id, self.kg_id, self.source_version_id) != (
            self.blob.org_id,
            self.blob.kg_id,
            self.blob.version_id,
        ):
            raise ValueError("artifacts must share their source namespace and version")
        return self
