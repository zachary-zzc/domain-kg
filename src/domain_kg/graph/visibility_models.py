"""Human visibility actions and current masks are independent of projection rows."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from domain_kg.models import (
    FrozenModel,
    IdempotencyKey,
    Namespace,
    NonNegative,
    ScopedRef,
)


class CurationCommand(FrozenModel):
    kg_id: UUID
    entity_id: UUID
    action: Literal["suppress", "restore"]
    expected_revision: NonNegative
    curation_scope: Literal["source", "kg_public"] = "source"
    source_ids: Annotated[tuple[UUID, ...], Field(max_length=100)] = ()
    reason: Annotated[str, Field(min_length=1, max_length=4000)]
    dry_run: bool = False
    idempotency_key: IdempotencyKey

    @model_validator(mode="after")
    def explicit_scope(self) -> Self:
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("source targets must be distinct")
        if self.curation_scope == "kg_public" and self.source_ids:
            raise ValueError("a public node fence has no source target list")
        return self


class CurationImpact(FrozenModel):
    dry_run: Literal[True] = True
    material_count: NonNegative
    affected_chunks: NonNegative
    affected_observations: NonNegative
    affected_supports: NonNegative
    target_revision: NonNegative


class CurationReceipt(Namespace):
    revision_id: UUID
    target_id: UUID
    action: Literal["suppress", "restore"]
    state: Literal["applied"] = "applied"
    target_revision: NonNegative
    visibility_revision: NonNegative
    job_id: UUID | None = None


class NodeFence(Namespace):
    entity_id: UUID
    curation: ScopedRef
    revision: Annotated[int, Field(ge=1, strict=True)]
    public: bool = False
    material_ids: Annotated[tuple[UUID, ...], Field(max_length=100)] = ()

    @model_validator(mode="after")
    def scoped_mask(self) -> Self:
        if (self.curation.org_id, self.curation.kg_id) != (self.org_id, self.kg_id):
            raise ValueError("node fence curation must share its namespace")
        if self.public == bool(self.material_ids) or len(set(self.material_ids)) != len(
            self.material_ids
        ):
            raise ValueError("choose source ownership or an explicit public fence")
        return self
