"""Bounded accepted repair provenance, separate from the historical curator."""

from typing import Self
from uuid import UUID

from pydantic import model_validator

from domain_kg.models import SHA256, ScopedRef, UTCDateTime
from domain_kg.revisions.projections import AcceptedProjection


class MaintenanceOrigin(ScopedRef):
    job_id: UUID
    attempt_id: UUID
    parent_manifest_id: UUID
    manifest_id: UUID
    base: AcceptedProjection

    @model_validator(mode="after")
    def scoped_base(self) -> Self:
        if (
            (self.base.org_id, self.base.kg_id) != (self.org_id, self.kg_id)
            or self.base.curation is None
            or self.base.source is not None
            or self.base.manifest_id == self.manifest_id
        ):
            raise ValueError("repair origin requires a distinct same-KG base curation")
        return self


class MaintenanceResult(MaintenanceOrigin):
    binding: AcceptedProjection
    published_revision: int
    content_hash: SHA256
    created_at: UTCDateTime

    @model_validator(mode="after")
    def same_content(self) -> Self:
        if (
            self.binding.curation != self.base.curation
            or self.binding.source is not None
            or self.binding.content_hash != self.content_hash
            or self.base.content_hash != self.content_hash
            or self.binding.manifest_id != self.manifest_id
            or self.binding.attempt_id != self.attempt_id
            or (self.binding.org_id, self.binding.kg_id) != (self.org_id, self.kg_id)
            or self.published_revision < 1
        ):
            raise ValueError("repair completion must preserve the sealed curation content")
        return self
