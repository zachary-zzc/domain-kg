"""A pinned portable snapshot envelope, not a live or undocumented native-store reader."""

from typing import Annotated, Literal

from pydantic import Field

from domain_kg.imports.legacy_models import ImportEdge, ImportNode, LegacyID
from domain_kg.models import SHA256, FrozenModel


class SnapshotDocument(FrozenModel):
    id: LegacyID
    sha256: SHA256


class LightRAGSnapshot(FrozenModel):
    format_version: Literal[1]
    engine_version: Literal["1.5.7"]
    consistent: Literal[True]
    documents: Annotated[tuple[SnapshotDocument, ...], Field(min_length=1, max_length=1000)]
    nodes: Annotated[tuple[ImportNode, ...], Field(max_length=1000)]
    edges: Annotated[tuple[ImportEdge, ...], Field(max_length=1000)]


def snapshot_records(body: bytes) -> tuple[tuple[ImportNode, ...], tuple[ImportEdge, ...]]:
    snapshot = LightRAGSnapshot.model_validate_json(body)
    documents = {item.id for item in snapshot.documents}
    if len(documents) != len(snapshot.documents) or any(
        not set(edge.source_ids).issubset(documents) for edge in snapshot.edges
    ):
        raise ValueError("snapshot source ownership differs")
    # A declared source checksum is provenance to reconcile, never proof of an
    # available original. The importer cannot promote these claims to verified.
    return snapshot.nodes, snapshot.edges
