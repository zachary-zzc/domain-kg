"""Canonical storage identities and attempt-specific derived records."""

from typing import Annotated, Self
from uuid import UUID, uuid5

from pydantic import Field, model_validator

from domain_kg.models import FrozenModel, ScopedRef


class MaterialRecord(ScopedRef):
    title: Annotated[str, Field(min_length=1, max_length=1000)]


class MaterialVersionRecord(ScopedRef):
    material: ScopedRef


class CurationRecord(ScopedRef):
    actor: Annotated[str, Field(min_length=1)]
    reason: Annotated[str, Field(min_length=1, max_length=4000)]


class ProjectionSchema(ScopedRef):
    source: ScopedRef
    generation: UUID
    attempt_id: UUID
    vector: Annotated[tuple[float, ...], Field(min_length=1, max_length=65536)]

    @model_validator(mode="after")
    def finite_vector(self) -> Self:
        import math

        if not all(math.isfinite(value) for value in self.vector):
            raise ValueError("vectors must contain finite values")
        return self

    @property
    def projection_id(self) -> UUID:
        # Every retry attempt and generation owns separate immutable projection IDs.
        return uuid5(self.org_id, f"{self.kg_id}/{self.id}/{self.generation}/{self.attempt_id}")


class IntegrityIssue(FrozenModel):
    record_id: UUID
    code: str
    field: str
