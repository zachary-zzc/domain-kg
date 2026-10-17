"""Bounded owner-only pending projection work; queue age is not receipt readiness."""

from datetime import UTC, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, ValidationError, field_validator, model_validator
from pymongo.errors import PyMongoError

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext, Action
from domain_kg.identity.policy import Policy
from domain_kg.jobs.models import JobStage
from domain_kg.models import Namespace, NonNegative, UTCDateTime

ProjectionKind = Literal[
    "ingest",
    "replace",
    "project",
    "repair",
    "restore_material",
    "reprocess_material",
    "curate",
    "maintenance",
    "generation",
]
PROJECTION_KINDS = (
    "ingest",
    "replace",
    "project",
    "repair",
    "restore_material",
    "reprocess_material",
    "curate",
    "maintenance",
    "generation",
)
PENDING_STAGES = (
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
)
SAMPLE_LIMIT = 1000


class _WorkRow(Namespace):
    id: UUID
    kind: ProjectionKind
    stage: JobStage
    created_at: UTCDateTime

    @field_validator("created_at", mode="before")
    @classmethod
    def bson_utc(cls, value: object) -> object:
        # BSON dates are UTC; the configured driver returns naive datetime objects.
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class ProjectionWork(Namespace):
    as_of: UTCDateTime
    sampled_jobs: Annotated[int, Field(ge=0, le=SAMPLE_LIMIT)]
    pending_jobs: NonNegative
    failed_jobs: NonNegative
    repair_required_jobs: NonNegative
    truncated: bool
    oldest_pending_at: UTCDateTime | None
    oldest_pending_seconds: NonNegative | None

    @model_validator(mode="after")
    def consistent_sample(self) -> Self:
        if self.sampled_jobs != self.pending_jobs + self.failed_jobs + self.repair_required_jobs:
            raise ValueError("work counters must describe the same bounded sample")
        if self.truncated and self.sampled_jobs != SAMPLE_LIMIT:
            raise ValueError("overflow requires a full bounded sample")
        if self.pending_jobs == 0:
            if self.oldest_pending_at is not None or self.oldest_pending_seconds is not None:
                raise ValueError("an empty pending sample has no oldest job")
        elif (
            self.oldest_pending_at is None
            or self.oldest_pending_seconds is None
            or self.oldest_pending_seconds
            != int((self.as_of - self.oldest_pending_at).total_seconds())
        ):
            raise ValueError("pending age must match its recorded admission time")
        return self


class ProjectionWorkMetrics:
    def __init__(self, policy: Policy) -> None:
        self.policy = policy

    async def get(self, access: AccessContext, kg: UUID) -> ProjectionWork:
        current = await self.policy.revalidate(access, action=Action.CONFIGURE)
        if kg not in current.kg_ids:
            raise inaccessible_reference()
        ns = {"org_id": str(current.org_id), "kg_id": str(kg)}
        try:
            raw = (
                await self.policy.database.jobs.find(
                    ns
                    | {
                        "kind": {"$in": list(PROJECTION_KINDS)},
                        "stage": {"$in": [*PENDING_STAGES, "failed", "repair_required"]},
                    },
                    {
                        "_id": 0,
                        "org_id": 1,
                        "kg_id": 1,
                        "id": 1,
                        "kind": 1,
                        "stage": 1,
                        "created_at": 1,
                    },
                )
                .sort([("created_at", 1), ("id", 1)])
                .limit(SAMPLE_LIMIT + 1)
                .max_time_ms(2000)
                .to_list()
            )
        except PyMongoError:
            raise DomainError(
                ErrorCode.BACKEND_UNAVAILABLE, "Projection work metrics are unavailable."
            ) from None
        timestamp = datetime.now(UTC)
        try:
            rows = [_WorkRow.model_validate(value) for value in raw]
            if (
                len(rows) > SAMPLE_LIMIT + 1
                or any(
                    (row.org_id, row.kg_id) != (current.org_id, kg)
                    or row.kind not in PROJECTION_KINDS
                    or row.stage not in (*PENDING_STAGES, "failed", "repair_required")
                    or row.created_at > timestamp
                    for row in rows
                )
                or len({row.id for row in rows}) != len(rows)
            ):
                raise ValueError("projection work rows differ from their scope")
            sample = rows[:SAMPLE_LIMIT]
            pending = [row for row in sample if row.stage in PENDING_STAGES]
            oldest = min((row.created_at for row in pending), default=None)
            result = ProjectionWork(
                org_id=current.org_id,
                kg_id=kg,
                as_of=timestamp,
                sampled_jobs=len(sample),
                pending_jobs=len(pending),
                failed_jobs=sum(row.stage == "failed" for row in sample),
                repair_required_jobs=sum(row.stage == "repair_required" for row in sample),
                truncated=len(rows) > SAMPLE_LIMIT,
                oldest_pending_at=oldest,
                oldest_pending_seconds=int((timestamp - oldest).total_seconds())
                if oldest
                else None,
            )
        except (ValidationError, ValueError, TypeError):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Projection work metrics are inconsistent."
            ) from None
        await self.policy.revalidate(current, action=Action.CONFIGURE)
        return result
