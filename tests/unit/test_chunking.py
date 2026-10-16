"""Exact multilingual source spans, deterministic IDs and measured token limits."""

from uuid import uuid4

import pytest
import tiktoken
from pydantic import ValidationError

from domain_kg.ingestion.models import ChunkConfig, ParsedBlock, ParsedDocument, SourceLocation
from domain_kg.materials.models import BlobRef


def document(text, *, kind="text", row=None):
    blob = BlobRef(
        org_id=uuid4(),
        kg_id=uuid4(),
        material_id=uuid4(),
        version_id=uuid4(),
        object_id=uuid4(),
        sha256="1" * 64,
        size_bytes=len(text.encode()),
        media_type="text/plain",
    )
    identifier = uuid4()
    block = ParsedBlock(
        org_id=blob.org_id,
        kg_id=blob.kg_id,
        material_id=blob.material_id,
        version_id=blob.version_id,
        id=identifier,
        text=text,
        location=SourceLocation(kind=kind, block_id=identifier, row=row),
    )
    return ParsedDocument(blob=blob, blocks=(block,), chunks=(), parser_version="fixture-v1")


def test_unicode_overlap_and_source_offsets_are_exact():
    from domain_kg.ingestion.chunking import chunk_document

    source = document("  细胞研究🔬 exact evidence.\n" * 120)
    config = ChunkConfig(target_tokens=32, overlap_tokens=8)
    chunks = chunk_document(source, config)
    assert len(chunks) > 1 and chunk_document(source, config) == chunks
    encoding = tiktoken.get_encoding("cl100k_base")
    text = source.blocks[0].text
    covered = set()
    previous = None
    for chunk in chunks:
        assert len(encoding.encode_ordinary(chunk.text)) <= 32
        for span in chunk.spans:
            assert span.quote == text[span.start : span.end]
            assert span.location == source.blocks[0].location
            covered.update(range(span.start, span.end))
        span = chunk.spans[0]
        if previous:
            assert span.start > previous.start and span.start <= previous.end
            assert len(encoding.encode_ordinary(text[span.start : previous.end])) <= 8
        previous = chunk.spans[-1]
    assert covered == set(range(len(text)))
    changed = chunk_document(source, ChunkConfig(target_tokens=24, overlap_tokens=4))
    assert changed[0].id != chunks[0].id


def test_small_table_rows_and_heading_locations_stay_whole():
    from domain_kg.ingestion.chunking import chunk_document

    source = document("Name | Evidence", kind="csv", row=2)
    chunks = chunk_document(source, ChunkConfig())
    assert len(chunks) == 1 and chunks[0].spans[0].location.row == 2
    assert chunks[0].text == source.blocks[0].text


def test_block_namespace_and_overlap_are_validated():
    with pytest.raises(ValidationError):
        ChunkConfig(target_tokens=32, overlap_tokens=32)
    source = document("Evidence")
    with pytest.raises(ValidationError):
        ParsedDocument.model_validate(
            source.model_copy(
                update={"blocks": (source.blocks[0].model_copy(update={"kg_id": uuid4()}),)}
            )
        )
