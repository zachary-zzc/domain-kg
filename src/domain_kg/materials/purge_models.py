"""Owner-authorized retained-data maintenance; source tombstones remain durable."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from domain_kg.materials.models import MaterialImpact
from domain_kg.models import FrozenModel, IdempotencyKey, Namespace, NonNegative


class PurgeMaterial(FrozenModel):
    kg_id: UUID
    material_id: UUID
    expected_revision: NonNegative
    reason: Annotated[str, Field(min_length=1, max_length=4000)]
    idempotency_key: IdempotencyKey
    dry_run: bool = False


class PurgeReceipt(Namespace):
    material_id: UUID
    revision: NonNegative
    state: Literal["preview", "pending", "purged"]
    job_id: UUID | None = None
    impact: MaterialImpact | None = None

    @model_validator(mode="after")
    def durable_cleanup(self) -> Self:
        if (self.state == "preview") != (self.job_id is None):
            raise ValueError("purge execution requires a durable job")
        if self.state == "preview" and self.impact is None:
            raise ValueError("a preview requires its current authorized impact")
        return self
