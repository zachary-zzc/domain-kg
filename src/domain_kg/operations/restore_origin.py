"""Typed durable origin of an accepted restored projection."""

from typing import Self
from uuid import UUID

from pydantic import model_validator

from domain_kg.models import SHA256, Namespace, ScopedRef, SourceVersion


class RestoredProjectionOrigin(Namespace):
    operation_id: UUID
    job_id: UUID
    source_kg_id: UUID
    source_backup_id: UUID
    source_header_sha: SHA256
    attempt_id: UUID
    root_manifest_id: UUID
    manifest_id: UUID
    projection_attempt_id: UUID
    generation: UUID
    content_hash: SHA256
    source: SourceVersion | None = None
    curation: ScopedRef | None = None

    @model_validator(mode="after")
    def distinct_scoped_origin(self) -> Self:
        owners = tuple(owner for owner in (self.source, self.curation) if owner is not None)
        if (
            self.source_kg_id == self.kg_id
            or len(owners) != 1
            or any((owner.org_id, owner.kg_id) != (self.org_id, self.kg_id) for owner in owners)
        ):
            raise ValueError(
                "restored projection requires a distinct source KG and exact target owner"
            )
        return self
