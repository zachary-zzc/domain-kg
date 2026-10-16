"""Compatibility boundary; extracted observations are unverified, never published here.

Uses upstream LightRAG APIs without copying upstream implementation. The probe
scope and identity map establish the adapter contract; authoritative resolution
and durable mappings are implemented by their later plan tasks.
"""

import asyncio
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from neo4j import AsyncDriver
from qdrant_client import AsyncQdrantClient, models


@dataclass(frozen=True)
class SourceChunk:
    chunk_id: str
    text: str
    source_id: str


@dataclass(frozen=True)
class UnverifiedObservation:
    chunk_id: str
    source_id: str
    name: str
    entity_type: str
    description: str


class LightRAGBoundary:
    """Only extracts source-bound observations; does not use global summaries."""

    def __init__(self) -> None:
        from domain_kg.engine.capabilities import checked_capabilities

        self.capabilities = checked_capabilities()

    async def extract(
        self,
        chunks: Sequence[SourceChunk],
        *,
        llm: Callable[..., Coroutine[Any, Any, str]],
    ) -> list[UnverifiedObservation]:
        from lightrag.operate import extract_entities

        if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
            raise ValueError("chunk IDs must be unique")
        source_map = {chunk.chunk_id: chunk.source_id for chunk in chunks}
        raw_chunks = {
            chunk.chunk_id: {
                "content": chunk.text,
                "tokens": len(chunk.text),
                "full_doc_id": chunk.source_id,
                "chunk_order_index": index,
                "file_path": chunk.source_id,
            }
            for index, chunk in enumerate(chunks)
        }
        config = {
            "role_llm_funcs": {"extract": llm},
            "entity_extract_max_gleaning": 0,
            "entity_extract_max_records": 20,
            "entity_extract_max_entities": 10,
            "entity_extraction_use_json": True,
            "llm_model_max_async": 2,
            "addon_params": {"language": "English"},
        }
        raw_results = await extract_entities(raw_chunks, config)
        observations = []
        for nodes, _edges in raw_results:
            for entries in nodes.values():
                for entry in entries:
                    chunk_id = str(entry["source_id"])
                    if chunk_id not in source_map:
                        raise ValueError("engine returned an unknown source chunk")
                    observations.append(
                        UnverifiedObservation(
                            chunk_id=chunk_id,
                            source_id=source_map[chunk_id],
                            name=str(entry["entity_name"]),
                            entity_type=str(entry["entity_type"]),
                            description=str(entry["description"]),
                        )
                    )
        return observations


class EngineIdentityMap:
    """Probe mapping: engine names/aliases resolve to independently assigned UUIDs."""

    def __init__(self) -> None:
        self._ids: dict[tuple[UUID, UUID, str], UUID] = {}

    def bind(self, org: UUID, kg: UUID, engine_key: str, entity: UUID) -> None:
        key = (org, kg, engine_key)
        if key in self._ids and self._ids[key] != entity:
            raise ValueError("engine identity is already bound")
        self._ids[key] = entity

    def resolve(self, org: UUID, kg: UUID, engine_key: str) -> UUID:
        return self._ids[(org, kg, engine_key)]


@dataclass(frozen=True)
class ProbeReadScope:
    """Non-serving spike scope; Task 2 defines the full authoritative ReadScope."""

    org: UUID
    kg: UUID
    accepted_versions: tuple[str, ...]
    generation: str


class ScopedProjectionProbe:
    """Explicit source filters, with shared clients and no per-request mutable state."""

    def __init__(self, graph: AsyncDriver, vectors: AsyncQdrantClient, collection: str) -> None:
        self._graph = graph
        self._vectors = vectors
        self._collection = collection

    async def graph_sources(self, scope: ProbeReadScope, entity: str) -> list[str]:
        if not scope.accepted_versions:
            return []
        records, _, _ = await self._graph.execute_query(
            "MATCH (f:ProbeFact)-[:SUPPORTED_BY]->(s:ProbeSource) "
            "WHERE f.org=$org AND f.kg=$kg AND s.org=$org AND s.kg=$kg "
            "AND f.entity=$entity AND f.generation=$generation "
            "AND f.version=s.version AND s.version IN $versions "
            "RETURN DISTINCT s.version AS version ORDER BY version LIMIT 50",
            org=str(scope.org),
            kg=str(scope.kg),
            entity=entity,
            generation=scope.generation,
            versions=list(scope.accepted_versions),
        )
        return [str(record["version"]) for record in records]

    async def vector_ids(
        self, scope: ProbeReadScope, vector: list[float], top_k: int
    ) -> list[int | str]:
        if not 1 <= top_k <= 50:
            raise ValueError("top_k must be between 1 and 50")
        if not scope.accepted_versions:
            return []
        conditions = [
            models.FieldCondition(key="org", match=models.MatchValue(value=str(scope.org))),
            models.FieldCondition(key="kg", match=models.MatchValue(value=str(scope.kg))),
            models.FieldCondition(
                key="generation", match=models.MatchValue(value=scope.generation)
            ),
            models.FieldCondition(
                key="version", match=models.MatchAny(any=list(scope.accepted_versions))
            ),
        ]
        result = await self._vectors.query_points(
            self._collection,
            query=vector,
            query_filter=models.Filter(must=[*conditions]),
            limit=top_k,
        )
        return [
            str(point.id) if isinstance(point.id, UUID) else point.id for point in result.points
        ]


async def run_blocking_engine[T](operation: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """Keep a synchronous upstream backend's entire lifecycle on one worker loop."""
    return await asyncio.to_thread(lambda: asyncio.run(operation()))
