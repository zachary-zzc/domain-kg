"""Typed human history omits grants, provider configuration and source snapshots."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from domain_kg.graph.curation_models import NodeFields
from domain_kg.graph.manual_relations import RelationFields
from domain_kg.models import FrozenModel, ManualProvenance, NonNegative, UTCDateTime


class HistoryRequest(FrozenModel):
    kg_id: UUID
    target_id: UUID
    kind: Literal["node", "relation"] = "node"
    limit: Annotated[int, Field(ge=1, le=100, strict=True)] = 25
    cursor: Annotated[str, Field(min_length=1, max_length=256)] | None = None


class HistoryEntry(FrozenModel):
    revision_id: UUID
    target_id: UUID
    target_revision: NonNegative
    state: Literal["pending", "accepted"]
    public: bool
    material_ids: tuple[UUID, ...]
    provenance: ManualProvenance
    created_at: UTCDateTime
    rollback_of: UUID | None = None
    job_id: UUID
    published_revision: NonNegative | None = None


class NodeHistoryEntry(HistoryEntry):
    kind: Literal["node"] = "node"
    before: NodeFields
    after: NodeFields


class RelationHistoryEntry(HistoryEntry):
    kind: Literal["relation"] = "relation"
    before: RelationFields
    after: RelationFields


class HistoryPage(FrozenModel):
    items: tuple[
        Annotated[NodeHistoryEntry | RelationHistoryEntry, Field(discriminator="kind")], ...
    ]
    next_cursor: str | None = None
