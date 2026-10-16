"""Checked upstream helper contracts; scope enforcement belongs to managed adapters."""

from dataclasses import dataclass
from importlib.metadata import version
from inspect import signature

from domain_kg.errors import DomainError, ErrorCode


@dataclass(frozen=True)
class EngineCapabilities:
    version: str = "1.5.7"
    source_mapping: bool = True
    scope_required: bool = True
    native_acl_filters: bool = False
    accepted_native_mutations: bool = False
    maintained_patch: str | None = None
    service_modes: tuple[str, ...] = ("fast", "deep", "vector", "lexical", "graph")


def checked_capabilities() -> EngineCapabilities:
    from lightrag.kg.qdrant_impl import QdrantVectorDBStorage
    from lightrag.operate import _perform_kg_search, extract_entities

    expected = EngineCapabilities()
    required_mix = {
        "query",
        "ll_keywords",
        "hl_keywords",
        "knowledge_graph_inst",
        "entities_vdb",
        "relationships_vdb",
        "text_chunks_db",
        "query_param",
        "chunks_vdb",
    }
    if (
        version("lightrag-hku") != expected.version
        or not {"chunks", "global_config"}.issubset(signature(extract_entities).parameters)
        or not required_mix.issubset(signature(_perform_kg_search).parameters)
        or tuple(signature(QdrantVectorDBStorage.query).parameters)
        != ("self", "query", "top_k", "query_embedding")
    ):
        raise DomainError(
            ErrorCode.BACKEND_UNAVAILABLE, "The installed LightRAG adapter contract is unreviewed."
        )
    return expected
