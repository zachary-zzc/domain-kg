"""Durable work receipts; request cancellation does not cancel accepted work."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from domain_kg.models import IdempotencyKey, Namespace, NonNegative, UTCDateTime

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
        "purge",
    ]
    target_id: UUID
    idempotency_key: IdempotencyKey


class LeasedJob(JobCommand):
    lease_token: UUID
    fence: NonNegative
    lease_until: UTCDateTime
    attempt_id: UUID
