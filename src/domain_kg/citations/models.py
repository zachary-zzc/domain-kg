"""Authorized citations contain true source spans or explicit human provenance."""

from typing import Literal
from uuid import UUID

from domain_kg.ingestion.models import Span
from domain_kg.models import FrozenModel, ManualProvenance


class Citation(FrozenModel):
    id: UUID
    kg_id: UUID
    kind: Literal["source", "manual"]
    text: str
    resolver_url: str
    material_id: UUID | None = None
    version_id: UUID | None = None
    chunk_id: UUID | None = None
    spans: tuple[Span, ...] = ()
    original_url: str | None = None
    curation_revision_id: UUID | None = None
    provenance: ManualProvenance | None = None
