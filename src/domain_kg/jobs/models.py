"""Durable work receipts; request cancellation does not cancel accepted work."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from domain_kg.materials.models import BlobRef
from domain_kg.models import (
    SHA256,
    FrozenModel,
    IdempotencyKey,
    Namespace,
    NonNegative,
    UTCDateTime,
)

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


class JobSource(FrozenModel):
    material_id: UUID
    version_id: UUID
    title: Annotated[str, Field(min_length=1, max_length=1000)]
    source: Annotated[str, Field(max_length=4000)] | None = None
    sha256: SHA256
    expected_active_version: UUID | None = None


class JobFailure(FrozenModel):
    code: str
    stage: JobStage | None = None
    request_id: UUID | None = None
    message: str
    action: str


class JobReceipt(Namespace):
    job_id: UUID
    stage: JobStage = "queued"
    progress: Annotated[float, Field(ge=0, le=1)] = 0
    repair_job_id: UUID | None = None
    warnings: tuple[str, ...] = ()
    kind: str | None = None
    created_at: UTCDateTime | None = None
    source: JobSource | None = None
    error: JobFailure | None = None


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
        "maintenance",
        "retention",
        "restore_cleanup",
        "generation",
        "restore_material",
        "reprocess_material",
        "purge_material",
        "curate",
        "purge",
    ]
    target_id: UUID
    idempotency_key: IdempotencyKey
    restore_intent_id: UUID | None = None

    @model_validator(mode="after")
    def restoration_intent(self) -> "JobCommand":
        if (self.kind in ("restore_material", "reprocess_material")) != (
            self.restore_intent_id is not None
        ):
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
