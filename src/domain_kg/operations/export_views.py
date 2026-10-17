"""Build complete bounded exports from the same qualified graph data as inspection."""

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.repository import GraphData, GraphRepository
from domain_kg.graph.views import EdgeView, NodeView


def export_views(
    data: GraphData, repository: GraphRepository, *, max_nodes: int = 1000, max_edges: int = 5000
) -> tuple[tuple[NodeView, ...], tuple[EdgeView, ...]]:
    if not 1 <= max_nodes <= 1000 or not 0 <= max_edges <= 5000:
        raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Export graph limits exceed their budget.")
    identifiers = {
        data.entity_id(observation.entity.id, observation.material_id)
        for observation in data.observations
    }
    identifiers.update(
        data.entity_id(manual.entity.id, context)
        for manual in data.manual_nodes
        for context in data.node_contexts(manual)
    )
    edges = tuple(repository.edge(row, data=data) for row in data.supports)
    identifiers.update(endpoint for edge in edges for endpoint in (edge.subject_id, edge.target_id))
    if len(identifiers) > max_nodes or len(edges) > max_edges:
        raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Export exceeds its complete graph budget.")
    nodes = []
    for identifier in sorted(identifiers, key=str):
        node = repository.node(data, identifier, resolve_alias=False)
        if node is None:
            raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "An export endpoint is missing.")
        if node.truncation:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "An export node requires a narrower scope.")
        nodes.append(node)
    return tuple(nodes), tuple(sorted(edges, key=lambda value: str(value.support_id)))
