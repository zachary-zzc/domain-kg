"""Source-qualified graph inspection responses; anchors never supply display fields."""

from uuid import UUID

from domain_kg.graph.curation_models import ManualNode
from domain_kg.ingestion.models import Span
from domain_kg.ingestion.resolved import ResolvedEntity
from domain_kg.models import FrozenModel, ManualProvenance, SourceVersion


class NodeView(FrozenModel):
    kg_id: UUID
    id: UUID
    name: str
    aliases: tuple[str, ...]
    descriptions: tuple[str, ...]
    entity_types: tuple[str, ...]
    observations: tuple[ResolvedEntity, ...]
    observation_count: int
    support_count: int
    neighbor_count: int
    truncation: bool = False
    curations: tuple[ManualNode, ...] = ()
    tags: tuple[str, ...] = ()


class EdgeView(FrozenModel):
    subject_id: UUID
    target_id: UUID
    assertion_id: UUID
    support_id: UUID
    predicate: str
    polarity: str
    confidence: float | None
    source: SourceVersion | None
    spans: tuple[Span, ...]
    curation_revision_id: UUID | None = None
    provenance: ManualProvenance | None = None
    status: str = "verified"
    description: str | None = None
    tags: tuple[str, ...] = ()


class SubgraphView(FrozenModel):
    kg_id: UUID
    revision: int
    nodes: tuple[NodeView, ...]
    edges: tuple[EdgeView, ...]
    truncation: bool = False
