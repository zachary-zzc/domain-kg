"""Management reads disclose material metadata without storage credentials or keys."""

from typing import Literal
from uuid import UUID

from domain_kg.ingestion.models import ParsedChunk
from domain_kg.materials.models import MaterialMetadata
from domain_kg.models import FrozenModel, NonNegative


class MaterialSummary(FrozenModel):
    id: UUID
    title: str
    revision: NonNegative
    active_version_id: UUID
    metadata: MaterialMetadata


class MaterialPage(FrozenModel):
    items: tuple[MaterialSummary, ...]
    next_cursor: str | None = None


class MaterialVersionSummary(FrozenModel):
    id: UUID
    state: Literal["accepted", "retained"]
    media_type: str
    size_bytes: NonNegative
    sha256: str


class MaterialDetail(FrozenModel):
    material: MaterialSummary
    versions: tuple[MaterialVersionSummary, ...]
    selected_version_id: UUID
    versions_truncated: bool = False
    chunks: tuple[ParsedChunk, ...] = ()
    chunks_truncated: bool = False
