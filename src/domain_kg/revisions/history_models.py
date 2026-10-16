"""Typed human history omits grants, provider configuration and source snapshots."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from domain_kg.graph.curation_models import NodeFields
from domain_kg.graph.identity_models import IdentityState
from domain_kg.graph.manual_relations import RelationFields
from domain_kg.models import FrozenModel, ManualProvenance, NonNegative, UTCDateTime


class HistoryRequest(FrozenModel):
    kg_id: UUID
    target_id: UUID
    kind: Literal["node", "relation"] = "node"
    include_suppressed: bool = False
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
    job_id: UUID | None = None
    published_revision: NonNegative | None = None


class NodeHistoryEntry(HistoryEntry):
    job_id: UUID = Field(...)
    kind: Literal["node"] = "node"
    before: NodeFields
    after: NodeFields


class RelationHistoryEntry(HistoryEntry):
    job_id: UUID = Field(...)
    kind: Literal["relation"] = "relation"
    before: RelationFields
    after: RelationFields


class VisibilityState(FrozenModel):
    suppressed: bool = Field(strict=True)


class VisibilityHistoryEntry(HistoryEntry):
    kind: Literal["visibility"] = "visibility"
    state: Literal["accepted"] = "accepted"
    job_id: None = None
    published_revision: None = None
    visibility_revision: NonNegative
    before: VisibilityState
    after: VisibilityState


class IdentityHistoryEntry(HistoryEntry):
    kind: Literal["identity"] = "identity"
    job_id: UUID = Field(...)
    operation: Literal["merge", "split"]
    member_ids: tuple[UUID, ...]
    before: IdentityState
    after: IdentityState


class IdentityControlState(FrozenModel):
    enabled: bool = Field(strict=True)


class IdentityControlHistoryEntry(HistoryEntry):
    kind: Literal["identity_control"] = "identity_control"
    job_id: UUID = Field(...)
    member_ids: tuple[UUID, ...]
    original_revision_id: UUID
    before: IdentityControlState
    after: IdentityControlState


class HistoryPage(FrozenModel):
    items: tuple[
        Annotated[
            NodeHistoryEntry
            | RelationHistoryEntry
            | VisibilityHistoryEntry
            | IdentityHistoryEntry
            | IdentityControlHistoryEntry,
            Field(discriminator="kind"),
        ],
        ...,
    ]
    next_cursor: str | None = None
