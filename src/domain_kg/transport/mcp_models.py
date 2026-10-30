"""Small structured views for agent workflows; no credentials or storage keys."""

from typing import Literal
from uuid import UUID

from domain_kg.catalog.backends import RegistryView
from domain_kg.graph.views import EdgeView, NodeView, SubgraphView
from domain_kg.identity.models import Group, Member
from domain_kg.materials.inspection_models import ImageSourceView
from domain_kg.models import FrozenModel, NonNegative
from domain_kg.providers.health import ConnectionCheck


class StorageChoice(FrozenModel):
    id: UUID
    revision: NonNegative


class StorageChoices(FrozenModel):
    items: tuple[StorageChoice, ...]
    next_cursor: str | None = None


class DomainChoice(FrozenModel):
    id: UUID
    name: str


class DomainChoices(FrozenModel):
    items: tuple[DomainChoice, ...]


class GraphOverview(FrozenModel):
    view: SubgraphView
    total_nodes: NonNegative
    page_index: NonNegative
    next_page_index: NonNegative | None


class EditView(FrozenModel):
    source_ids: tuple[UUID, ...]
    public: bool
    expected_revision: NonNegative
    node: NodeView | None
    edges: tuple[EdgeView, ...]


class ImagePage(FrozenModel):
    items: tuple[ImageSourceView, ...]


class FileChunk(FrozenModel):
    content_base64: str
    offset: NonNegative
    next_offset: NonNegative
    total_bytes: NonNegative
    eof: bool


class TeamPage(FrozenModel):
    items: tuple[Member | Group, ...]
    next_cursor: str | None = None


class ApprovedReference(FrozenModel):
    kind: Literal["mongo", "neo4j", "qdrant", "s3"]
    secret_ref: UUID


class ApprovedReferences(FrozenModel):
    items: tuple[ApprovedReference, ...]
    next_cursor: str | None = None


class BackendPage(FrozenModel):
    items: tuple[RegistryView, ...]
    next_cursor: str | None = None


class ModelNames(FrozenModel):
    models: tuple[str, ...]


class ModelSaveResult(FrozenModel):
    revision: NonNegative | None
    ready: bool
    check: ConnectionCheck | None = None
