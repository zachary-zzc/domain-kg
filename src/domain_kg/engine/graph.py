"""Request-bound LightRAG graph reads; no global descriptions or mutable workspace."""

from typing import Any
from uuid import UUID

from domain_kg.graph.repository import GraphRepository
from domain_kg.revisions.models import ReadScope


class ScopedLightRAGGraph:
    def __init__(self, repository: GraphRepository, scope: ReadScope) -> None:
        self.repository, self.scope = repository, scope

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        data = await self.repository.open(self.scope)
        node = self.repository.node(data, UUID(node_id))
        await self.repository.resolver.revalidate(self.scope)
        if node is None:
            return None
        return {
            "entity_id": str(node.id),
            "entity_name": node.name,
            "entity_type": ";".join(node.entity_types),
            "description": "\n".join(node.descriptions),
            "source_id": "<SEP>".join(str(item.chunk_id) for item in node.observations),
        }

    async def get_node_edges(self, node_id: str) -> list[tuple[str, str]]:
        data = await self.repository.open(self.scope)
        rows = await self.repository.adjacent(data, (UUID(node_id),), 400)
        await self.repository.resolver.revalidate(self.scope)
        return [
            (str(row.assertion.assertion.subject.id), str(row.assertion.assertion.target.id))
            for row in rows
            if row.assertion
        ]
