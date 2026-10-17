"""A rebuild records current work separately from the true sealed artifact owner."""

from typing import Self
from uuid import UUID

from pydantic import model_validator

from domain_kg.models import SHA256, NonNegative, ScopedRef, UTCDateTime
from domain_kg.revisions.projections import AcceptedProjection


class GenerationOrigin(ScopedRef):
    job_id: UUID
    input_id: UUID
    attempt_id: UUID
    manifest_id: UUID
    root_manifest_id: UUID
    parent_manifest_id: UUID
    base: AcceptedProjection

    @model_validator(mode="after")
    def namespace(self) -> Self:
        if (self.base.org_id, self.base.kg_id) != (
            self.org_id,
            self.kg_id,
        ) or self.base.manifest_id == self.manifest_id:
            raise ValueError("generation ancestry must preserve a distinct same-KG artifact owner")
        return self


class GenerationResult(ScopedRef):
    job_id: UUID
    attempt_id: UUID
    root_manifest_id: UUID
    generation: UUID
    published_revision: NonNegative
    inputs_hash: SHA256
    bindings_hash: SHA256
    binding_count: NonNegative
    created_at: UTCDateTime
