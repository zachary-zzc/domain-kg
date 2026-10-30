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


def document_blocks(texts):
    source = document("\n".join(texts))
    blocks = []
    for text in texts:
        identifier = uuid4()
        blocks.append(
            source.blocks[0].model_copy(
                update={
                    "id": identifier,
                    "text": text,
                    "location": SourceLocation(kind="text", block_id=identifier),
                }
            )
        )
    return source.model_copy(update={"blocks": tuple(blocks)})


@pytest.mark.parametrize(
    "first", ["Alpha evidence. " * 12, "Alpha evidence. " * 60], ids=["normal-block", "long-block"]
)
def test_adjacent_blocks_keep_a_bounded_tail_when_previous_block_exceeds_overlap(first):
    from domain_kg.ingestion.chunking import chunk_document, tokens

    source = document_blocks([first, "Beta evidence. " * 12])
    chunks = chunk_document(source, ChunkConfig(target_tokens=64, overlap_tokens=10))
    boundary = next(
        i for i, c in enumerate(chunks) if c.spans[-1].location == source.blocks[1].location
    )
    assert boundary > 0
    previous = chunks[boundary - 1].spans[-1]
    overlap = chunks[boundary].spans[0]
    assert overlap.location == previous.location
    assert previous.start < overlap.start < previous.end
    shared = first[overlap.start : previous.end]
    assert 0 < tokens(shared) <= 10
    assert all(tokens(chunk.text) <= 64 for chunk in chunks)
    assert ParsedDocument.model_validate(source.model_copy(update={"chunks": tuple(chunks)}))
    for block in source.blocks:
        covered = {
            i
            for c in chunks
            for s in c.spans
            if s.location == block.location
            for i in range(s.start, s.end)
        }
        assert covered == set(range(len(block.text)))


@pytest.mark.parametrize("boundary", ["heading", "table", "disabled"])
def test_overlap_preserves_section_boundaries_and_intact_small_table_rows(boundary):
    from domain_kg.ingestion.chunking import chunk_document

    source = document_blocks(["Alpha evidence. " * 12, "Beta evidence. " * 12])
    config = ChunkConfig(target_tokens=64, overlap_tokens=0 if boundary == "disabled" else 10)
    blocks = []
    for i, block in enumerate(source.blocks):
        changes = (
            {"kind": "csv", "table": 0, "row": i} if boundary == "table" else {"heading": str(i)}
        )
        if boundary != "disabled":
            block = block.model_copy(update={"location": block.location.model_copy(update=changes)})
        blocks.append(block)
    source = source.model_copy(update={"blocks": tuple(blocks)})
    chunks = chunk_document(source, config)
    assert len(chunks) == 2
    assert [chunk.text for chunk in chunks] == [block.text for block in blocks]
    assert all(len(chunk.spans) == 1 for chunk in chunks)


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
