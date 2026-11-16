"""Parser and untrusted extraction output contracts. Output is data, never commands."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from domain_kg.materials.models import BlobRef
from domain_kg.models import SHA256, FrozenModel, NonNegative, PreservedText, SourceVersion


class ParseOptions(FrozenModel):
    memory_limit_mib: Annotated[int, Field(ge=64, le=8192, strict=True)] = 1024
    max_pages: Annotated[int, Field(ge=1, le=500, strict=True)] = 500
    max_expanded_bytes: Annotated[int, Field(ge=1, le=100 * 1024 * 1024, strict=True)] = (
        100 * 1024 * 1024
    )
    timeout_seconds: Annotated[int, Field(ge=1, le=300, strict=True)] = 120
    ocr: bool = True
    target_tokens: Annotated[int, Field(ge=1, le=700, strict=True)] = 700
    overlap_tokens: Annotated[int, Field(ge=0, le=100, strict=True)] = 100

    @model_validator(mode="after")
    def bounded_overlap(self) -> Self:
        if self.overlap_tokens >= self.target_tokens:
            raise ValueError("overlap must be smaller than chunk size")
        return self


class ChunkConfig(FrozenModel):
    target_tokens: Annotated[int, Field(ge=1, le=32768, strict=True)] = 700
    overlap_tokens: Annotated[int, Field(ge=0, le=100, strict=True)] = 100

    @model_validator(mode="after")
    def bounded_overlap(self) -> Self:
        if self.overlap_tokens >= self.target_tokens:
            raise ValueError("overlap must be smaller than chunk size")
        return self


class SourceLocation(FrozenModel):
    kind: Literal["pdf", "docx", "xlsx", "pptx", "text", "html", "csv", "json", "image"]
    block_id: UUID
    page: Annotated[int, Field(ge=1, le=500, strict=True)] | None = None
    paragraph: NonNegative | None = None
    table: NonNegative | None = None
    row: NonNegative | None = None
    json_pointer: str | None = None
    heading: str | None = None
    sheet: str | None = Field(default=None, max_length=200, exclude_if=lambda v: v is None)
    slide: Annotated[int, Field(ge=1, le=500)] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    section: Literal["notes", "header", "footer"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    image_id: UUID | None = Field(default=None, exclude_if=lambda v: v is None)
    interpretation_model: str | None = Field(
        default=None, max_length=200, exclude_if=lambda v: v is None
    )
    interpretation_profile_id: UUID | None = Field(default=None, exclude_if=lambda v: v is None)
    interpretation_profile_revision: int | None = Field(
        default=None, ge=1, exclude_if=lambda v: v is None
    )
    # Omit the default to preserve sealed source/projection payloads from before OCR.
    text_origin: Literal["native", "ocr", "vision"] = Field(
        default="native", exclude_if=lambda value: value == "native"
    )

    @model_validator(mode="after")
    def physical_location(self) -> Self:
        if self.text_origin == "ocr" and self.kind not in ("pdf", "image"):
            raise ValueError("OCR provenance requires PDF or image evidence")
        if self.text_origin == "vision" and (self.kind != "image" or self.image_id is None):
            raise ValueError("vision provenance requires an image source")
        if self.kind == "image" and self.image_id is None:
            raise ValueError("image evidence requires a source image")
        if self.kind == "pdf" and self.page is None:
            raise ValueError("PDF requires a physical page number")
        if self.page is not None and self.kind not in ("pdf", "image"):
            raise ValueError("only PDF/image evidence can have page numbers")
        if self.kind == "docx" and self.paragraph is None and self.table is None:
            raise ValueError("DOCX requires a paragraph or table location")
        if self.kind == "csv" and self.row is None:
            raise ValueError("CSV requires a row location")
        if self.kind == "json" and self.json_pointer is None:
            raise ValueError("JSON requires a pointer")
        if self.kind == "xlsx" and (self.sheet is None or self.row is None):
            raise ValueError("XLSX requires worksheet and row")
        if self.kind == "pptx" and self.slide is None:
            raise ValueError("PPTX requires a slide")
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


def span_status(spans: tuple[Span, ...]) -> Literal["verified", "model_interpreted"]:
    return (
        "model_interpreted"
        if any(span.location.text_origin == "vision" for span in spans)
        else "verified"
    )


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


class ImageOrigin(FrozenModel):
    part: Annotated[str, Field(min_length=1, max_length=1000)]
    page: Annotated[int, Field(ge=1, le=500)] | None = None
    slide: Annotated[int, Field(ge=1, le=500)] | None = None
    sheet: str | None = None


class SourceImage(SourceVersion):
    id: UUID
    media_type: Literal[
        "image/png", "image/jpeg", "image/webp", "image/gif", "image/tiff", "image/bmp"
    ]
    sha256: SHA256
    size_bytes: Annotated[int, Field(ge=1, le=20 * 1024 * 1024)]
    width: Annotated[int, Field(ge=1, le=40000)]
    height: Annotated[int, Field(ge=1, le=40000)]
    origins: Annotated[tuple[ImageOrigin, ...], Field(min_length=1, max_length=1000)]
    data_base64: Annotated[str, Field(max_length=28 * 1024 * 1024)] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    blob: BlobRef | None = Field(default=None, exclude_if=lambda v: v is None)
    preview_sha256: SHA256
    preview_base64: Annotated[str, Field(max_length=6 * 1024 * 1024)] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    preview_blob: BlobRef | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def image_ownership(self) -> Self:
        import base64
        import hashlib

        if (self.data_base64 is None) == (self.blob is None):
            raise ValueError("image requires exactly one raw payload or stored blob")
        if self.width * self.height > 40_000_000:
            raise ValueError("image exceeds the pixel limit")
        if self.blob is not None:
            if any(getattr(self.blob, f) != getattr(self, f) for f in SourceVersion.model_fields):
                raise ValueError("image blob must share its parent source")
            if (self.blob.sha256, self.blob.size_bytes, self.blob.media_type) != (
                self.sha256,
                self.size_bytes,
                self.media_type,
            ):
                raise ValueError("image blob differs from the original image")
        else:
            data = base64.b64decode(self.data_base64 or "", validate=True)
            if len(data) != self.size_bytes or hashlib.sha256(data).hexdigest() != self.sha256:
                raise ValueError("image checksum differs")
        if (self.preview_base64 is None) == (self.preview_blob is None):
            raise ValueError("image requires an isolated raster preview")
        if self.preview_blob is not None:
            if any(
                getattr(self.preview_blob, f) != getattr(self, f)
                for f in SourceVersion.model_fields
            ):
                raise ValueError("preview must share its parent source")
            if (
                self.preview_blob.sha256 != self.preview_sha256
                or self.preview_blob.media_type != "image/png"
                or self.preview_blob.size_bytes > 4 * 1024 * 1024
            ):
                raise ValueError("image preview checksum/type differs")
        else:
            preview = base64.b64decode(self.preview_base64 or "", validate=True)
            if (
                len(preview) > 4 * 1024 * 1024
                or hashlib.sha256(preview).hexdigest() != self.preview_sha256
            ):
                raise ValueError("image preview checksum/size differs")
        return self


class ParsedDocument(FrozenModel):
    blob: BlobRef
    chunks: tuple[ParsedChunk, ...] = ()
    blocks: Annotated[tuple[ParsedBlock, ...], Field(max_length=100000)] = ()
    parser_version: str = "legacy"
    warnings: tuple[str, ...] = ()
    images: Annotated[tuple[SourceImage, ...], Field(max_length=128)] = Field(
        default=(), exclude_if=lambda v: not v
    )

    @model_validator(mode="after")
    def scoped_chunks(self) -> Self:
        for chunk in (*self.chunks, *self.blocks, *self.images):
            if any(
                getattr(chunk, field) != getattr(self.blob, field)
                for field in ("org_id", "kg_id", "material_id", "version_id")
            ):
                raise ValueError("parsed chunks must belong to the original blob")
        image_ids = {image.id for image in self.images}
        if len(image_ids) != len(self.images):
            raise ValueError("image source IDs must be unique")
        for image_block in self.blocks:
            if (
                image_block.location.image_id is not None
                and image_block.location.image_id not in image_ids
            ):
                raise ValueError("image text must refer to an owned image source")
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
    aliases: Annotated[tuple[str, ...], Field(max_length=30)] = ()
    identifier: Annotated[str, Field(max_length=512)] | None = None
    spans: Annotated[tuple[Span, ...], Field(min_length=1)]


class ExtractedAssertion(FrozenModel):
    subject: str
    target: str
    predicate: str
    confidence: Annotated[float, Field(ge=0, le=1)]
    polarity: Literal["positive", "negative", "uncertain"] = "positive"
    qualifiers: Annotated[tuple["ExtractedQualifier", ...], Field(max_length=10)] = ()
    spans: Annotated[tuple[Span, ...], Field(min_length=1)]


class ExtractedQualifier(FrozenModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    value: Annotated[str, Field(min_length=1, max_length=1000)]
    spans: Annotated[tuple[Span, ...], Field(min_length=1, max_length=20)]


class ExtractionContent(FrozenModel):
    entities: Annotated[tuple[ExtractedEntity, ...], Field(max_length=50)]
    assertions: Annotated[tuple[ExtractedAssertion, ...], Field(max_length=100)]


class ExtractionResult(ExtractionContent):
    """Server validation outcome; warnings are never part of the model's output schema."""

    # A two-pass result can contain the union of two bounded model responses.
    entities: Annotated[tuple[ExtractedEntity, ...], Field(max_length=100)]
    assertions: Annotated[tuple[ExtractedAssertion, ...], Field(max_length=200)]

    warnings: tuple[
        Literal["extraction_gleaning_context_limit", "extraction_gleaning_incomplete"]
        | Annotated[
            str,
            Field(pattern=r"^extraction_rejected_(entities|assertions):[1-9][0-9]{0,8}$"),
        ],
        ...,
    ] = Field(default=(), exclude_if=lambda value: not value)


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
