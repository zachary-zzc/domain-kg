"""Small display-only adapter for an already authorized GraphService neighborhood."""

from typing import TypedDict
from urllib.parse import urlencode
from uuid import UUID

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.views import SubgraphView


class GraphNode(TypedDict):
    id: str
    name: str
    href: str


class GraphEdge(TypedDict):
    id: str
    source: str
    target: str
    predicate: str
    status: str
    href: str


class GraphData(TypedDict):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated: bool


def graph_data(
    view: SubgraphView,
    kg: UUID,
    *,
    include_unverified: bool = False,
    material_id: UUID | None = None,
    version_id: UUID | None = None,
) -> GraphData:
    identifiers = {node.id for node in view.nodes}
    edge_ids = {f"{edge.assertion_id}:{edge.support_id}" for edge in view.edges}
    if (
        view.kg_id != kg
        or len(view.nodes) > 25
        or len(view.edges) > 40
        or len(identifiers) != len(view.nodes)
        or len(edge_ids) != len(view.edges)
        or any(node.kg_id != kg for node in view.nodes)
        or any(
            edge.subject_id not in identifiers
            or edge.target_id not in identifiers
            or edge.status not in {"verified", "manual", "imported_unverified"}
            for edge in view.edges
        )
    ):
        raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Graph display references differ.")
    query = {"include_unverified": "true" if include_unverified else "false"}
    if material_id is not None:
        query["material_id"] = str(material_id)
    if version_id is not None:
        query["version_id"] = str(version_id)
    suffix = "?" + urlencode(query)
    return {
        "nodes": [
            {
                "id": str(node.id),
                "name": node.name,
                "href": f"/admin/kgs/{kg}/nodes/{node.id}{suffix}",
            }
            for node in view.nodes
        ],
        "edges": [
            {
                "id": f"{edge.assertion_id}:{edge.support_id}",
                "source": str(edge.subject_id),
                "target": str(edge.target_id),
                "predicate": edge.predicate,
                "status": edge.status,
                "href": f"/admin/kgs/{kg}/relations/{edge.assertion_id}/history{suffix}",
            }
            for edge in view.edges
        ],
        "truncated": view.truncation,
    }
