"""Parser and untrusted extraction output contracts. Output is data, never commands."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from domain_kg.materials.models import BlobRef
from domain_kg.models import FrozenModel, NonNegative, PreservedText, SourceVersion


class ParseOptions(FrozenModel):
    max_pages: Annotated[int, Field(ge=1, le=500, strict=True)] = 500
    max_expanded_bytes: Annotated[int, Field(ge=1, le=100 * 1024 * 1024, strict=True)] = (
        100 * 1024 * 1024
    )
    timeout_seconds: Annotated[int, Field(ge=1, le=300, strict=True)] = 120
    ocr: bool = False
    target_tokens: Annotated[int, Field(ge=1, le=700, strict=True)] = 700
    overlap_tokens: Annotated[int, Field(ge=0, le=100, strict=True)] = 100

    @model_validator(mode="after")
    def bounded_overlap(self) -> Self:
        if self.overlap_tokens >= self.target_tokens:
            raise ValueError("overlap must be smaller than chunk size")
        return self


class ChunkConfig(FrozenModel):
    target_tokens: Annotated[int, Field(ge=1, le=700, strict=True)] = 700
    overlap_tokens: Annotated[int, Field(ge=0, le=100, strict=True)] = 100

    @model_validator(mode="after")
    def bounded_overlap(self) -> Self:
        if self.overlap_tokens >= self.target_tokens:
            raise ValueError("overlap must be smaller than chunk size")
        return self


class SourceLocation(FrozenModel):
    kind: Literal["pdf", "docx", "text", "html", "csv", "json"]
    block_id: UUID
    page: Annotated[int, Field(ge=1, le=500, strict=True)] | None = None
    paragraph: NonNegative | None = None
    table: NonNegative | None = None
    row: NonNegative | None = None
    json_pointer: str | None = None
    heading: str | None = None

    @model_validator(mode="after")
    def physical_location(self) -> Self:
        if (self.kind == "pdf") != (self.page is not None):
            raise ValueError("only PDF locations require physical page numbers")
        if self.kind == "docx" and self.paragraph is None and self.table is None:
            raise ValueError("DOCX requires a paragraph or table location")
        if self.kind == "csv" and self.row is None:
            raise ValueError("CSV requires a row location")
        if self.kind == "json" and self.json_pointer is None:
            raise ValueError("JSON requires a pointer")
        return self


class Span(FrozenModel):
    location: SourceLocation
    start: NonNegative
    end: Annotated[int, Field(ge=1, strict=True)]
    quote: Annotated[PreservedText, Field(min_length=1)]

    @model_validator(mode="after")
    def nonempty_span(self) -> Self:
        if self.end <= self.start or self.end - self.start != len(self.quote):
            raise ValueError("span offsets must match the quoted text length")
        return self


class ParsedChunk(SourceVersion):
    id: UUID
    text: PreservedText
    spans: tuple[Span, ...]
    parser_version: str
    chunker_version: str


class ParsedBlock(SourceVersion):
    id: UUID
    text: PreservedText
    location: SourceLocation

    @model_validator(mode="after")
    def block_location(self) -> Self:
        if self.id != self.location.block_id:
            raise ValueError("block location identity must match the block")
        return self


class ParsedDocument(FrozenModel):
    blob: BlobRef
    chunks: tuple[ParsedChunk, ...] = ()
    blocks: Annotated[tuple[ParsedBlock, ...], Field(max_length=100000)] = ()
    parser_version: str = "legacy"
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def scoped_chunks(self) -> Self:
        for chunk in (*self.chunks, *self.blocks):
            if any(
                getattr(chunk, field) != getattr(self.blob, field)
                for field in ("org_id", "kg_id", "material_id", "version_id")
            ):
                raise ValueError("parsed chunks must belong to the original blob")
        blocks = {block.id: block for block in self.blocks}
        if len(blocks) != len(self.blocks):
            raise ValueError("source block IDs must be unique")
        if blocks:
            for chunk in self.chunks:
                for span in chunk.spans:
                    block = blocks.get(span.location.block_id)
                    if (
                        block is None
                        or span.location != block.location
                        or (block.text[span.start : span.end] != span.quote)
                    ):
                        raise ValueError("chunk spans must match their immutable source blocks")
        return self


class ExtractedEntity(FrozenModel):
    local_id: Annotated[str, Field(min_length=1, max_length=200)]
    name: Annotated[str, Field(min_length=1, max_length=1000)]
    entity_type: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str, Field(max_length=8000)] = ""
    spans: Annotated[tuple[Span, ...], Field(min_length=1)]


class ExtractedAssertion(FrozenModel):
    subject: str
    target: str
    predicate: str
    confidence: Annotated[float, Field(ge=0, le=1)]
    spans: Annotated[tuple[Span, ...], Field(min_length=1)]


class ExtractionBatch(SourceVersion):
    chunk_id: UUID
    schema_id: UUID
    schema_revision: NonNegative
    entities: tuple[ExtractedEntity, ...]
    assertions: tuple[ExtractedAssertion, ...]
    provider_profile_id: UUID
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def local_endpoints(self) -> Self:
        ids = [entity.local_id for entity in self.entities]
        if len(ids) != len(set(ids)):
            raise ValueError("extraction entity IDs must be distinct")
        if any(
            assertion.subject not in ids or assertion.target not in ids
            for assertion in self.assertions
        ):
            raise ValueError("extraction endpoints must be in the same batch")
        return self
