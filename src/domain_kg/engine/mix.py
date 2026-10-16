"""Pinned native mix orchestration over a single authenticated, bounded request view.

Recall is completed outside native helpers that swallow exceptions. Their adapters
read only the verified request data and never issue unrestricted backend calls.
"""

from typing import Any
from uuid import uuid4

from domain_kg.engine.lightrag import LightRAGBoundary
from domain_kg.graph.views import SubgraphView
from domain_kg.retrieval.models import EvidenceCandidate
from domain_kg.storage.neo4j import inconsistent


class BoundedNativeGraph:
    def __init__(self, graph: SubgraphView | None) -> None:
        self.nodes = (
            {
                str(node.id): {
                    "entity_name": str(node.id),
                    "entity_type": ";".join(node.entity_types),
                    "description": "\n".join(node.descriptions),
                    "source_id": "<SEP>".join(
                        [str(item.chunk_id) for item in node.observations]
                        + [f"curation:{item.curation.id}" for item in node.curations]
                    ),
                }
                for node in graph.nodes
            }
            if graph
            else {}
        )
        self.edges: dict[tuple[str, str], dict[str, Any]] = {}
        if graph:
            for edge in graph.edges:
                left, right = sorted((str(edge.subject_id), str(edge.target_id)))
                pair = (left, right)
                value = self.edges.setdefault(
                    pair,
                    {
                        "src_id": pair[0],
                        "tgt_id": pair[1],
                        "weight": 1.0,
                        "description": [],
                        "support_ids": [],
                        "assertion_ids": [],
                    },
                )
                value["description"].append(f"{edge.predicate} ({edge.polarity})")
                value["support_ids"].append(str(edge.support_id))
                value["assertion_ids"].append(str(edge.assertion_id))
            for value in self.edges.values():
                value["description"] = "\n".join(value["description"])

    async def get_nodes_batch(self, identifiers: list[str]) -> dict[str, dict[str, Any]]:
        return {key: dict(self.nodes[key]) for key in identifiers if key in self.nodes}

    async def get_nodes_edges_batch(
        self, identifiers: list[str]
    ) -> dict[str, list[tuple[str, str]]]:
        return {key: [pair for pair in self.edges if key in pair] for key in identifiers}

    async def node_degrees_batch(self, identifiers: list[str]) -> dict[str, int]:
        return {key: sum(key in pair for pair in self.edges) for key in identifiers}

    async def get_edges_batch(
        self, pairs: list[dict[str, str]]
    ) -> dict[tuple[str, str], dict[str, Any]]:
        return {
            (item["src"], item["tgt"]): dict(self.edges[pair])
            for item in pairs
            if (pair := tuple(sorted((item["src"], item["tgt"])))) in self.edges
        }

    async def edge_degrees_batch(self, pairs: list[tuple[str, str]]) -> dict[tuple[str, str], int]:
        degrees = await self.node_degrees_batch(list(self.nodes))
        return {pair: sum(degrees.get(key, 0) for key in pair) for pair in pairs}


class RecallIndex:
    cosine_better_than_threshold = -1.0

    def __init__(self, key: str, rows: list[dict[str, Any]]) -> None:
        self.key, self.rows = key, rows

    async def query(
        self, query: str, top_k: int, query_embedding: list[float] | None = None
    ) -> list[dict[str, Any]]:
        if query != self.key or query_embedding is not None or not 1 <= top_k <= 400:
            raise inconsistent()
        return [dict(item) for item in self.rows[:top_k]]


def candidate_native_id(item: EvidenceCandidate) -> str:
    return f"curation:{item.curation_revision_id}" if item.kind == "manual" else str(item.chunk_id)


async def native_mix(
    graph: SubgraphView | None, vectors: list[EvidenceCandidate]
) -> dict[str, Any]:
    from lightrag import QueryParam
    from lightrag.operate import _perform_kg_search

    LightRAGBoundary()  # Refuse an unverified SDK version.
    adapter = BoundedNativeGraph(graph)
    key = str(uuid4())  # Upstream logs query/keywords; keep source and query text out.
    entities = RecallIndex(key, [{"entity_name": node} for node in adapter.nodes])
    relations = RecallIndex(key, [{"src_id": pair[0], "tgt_id": pair[1]} for pair in adapter.edges])
    chunks = RecallIndex(
        key,
        [
            {
                "id": candidate_native_id(item),
                "content": item.text,
                "distance": item.score,
                "file_path": (
                    f"kg:{item.kg_id}/curation:{item.curation_revision_id}"
                    if item.kind == "manual"
                    else f"kg:{item.kg_id}/version:{item.version_id}/chunk:{item.chunk_id}"
                ),
            }
            for item in vectors
        ],
    )

    class ChunkSettings:
        global_config = {"kg_chunk_pick_method": "WEIGHT"}
        embedding_func = None  # Already embedded once under the approved space.

    result: dict[str, Any] = await _perform_kg_search(
        query=key,
        ll_keywords=key,
        hl_keywords=key,
        knowledge_graph_inst=adapter,
        entities_vdb=entities,
        relationships_vdb=relations,
        text_chunks_db=ChunkSettings(),
        query_param=QueryParam(mode="mix", top_k=400, chunk_top_k=50),
        chunks_vdb=chunks,
    )
    if (
        {item["entity_name"] for item in result["final_entities"]} != set(adapter.nodes)
        or {item["chunk_id"] for item in result["vector_chunks"]}
        != {candidate_native_id(item) for item in vectors}
        or {
            tuple(sorted(item.get("src_tgt", (item.get("src_id"), item.get("tgt_id")))))
            for item in result["final_relations"]
        }
        != set(adapter.edges)
    ):
        raise inconsistent()
    return result
