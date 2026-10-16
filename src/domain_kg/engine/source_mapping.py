"""Opaque native engine names: material text never selects a graph/server identity."""

from uuid import uuid5

from domain_kg.ingestion.models import ParsedChunk


def engine_name(chunk: ParsedChunk, local_id: str) -> str:
    return (
        "DKG_" + uuid5(chunk.org_id, f"{chunk.kg_id}/{chunk.version_id}/{chunk.id}/{local_id}").hex
    )


def engine_source(chunk: ParsedChunk) -> str:
    return f"{chunk.org_id}/{chunk.kg_id}/{chunk.version_id}/{chunk.id}"
