"""Deterministic offline format decoders, invoked only in the bounded parser process."""

import csv
import hashlib
import io
import json
from html.parser import HTMLParser
from typing import Any
from uuid import uuid5

from defusedxml.ElementTree import fromstring

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.ingestion.file_types import (
    IMAGE_TYPES,
    LEGACY,
    ODP,
    ODS,
    OFFICE_VERSION,
    PPTX,
    XLS,
    XLSX,
)
from domain_kg.ingestion.models import (
    ImageOrigin,
    ParsedBlock,
    ParsedDocument,
    ParseOptions,
    SourceImage,
    SourceLocation,
)
from domain_kg.ingestion.pdf import VERSION as PDF_VERSION
from domain_kg.ingestion.pdf import parse_pdf
from domain_kg.materials.models import BlobRef

WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class Blocks:
    def __init__(self, blob: BlobRef, options: ParseOptions, parser_version: str) -> None:
        self.blob, self.options, self.version = blob, options, parser_version
        self.items: list[ParsedBlock] = []
        self.bytes = 0
        self.warnings: list[str] = []
        self.images: list[SourceImage] = []

    def add(self, text: str, kind: str, **location: Any) -> None:
        if not text:
            return
        self.bytes += len(text.encode()) + len(str(location.get("heading", "")).encode())
        if self.bytes > self.options.max_expanded_bytes or len(self.items) >= 100000:
            raise DomainError(
                ErrorCode.QUOTA_EXCEEDED, "Expanded document exceeds the parser limit."
            )
        identity = hashlib.sha256(
            (text + json.dumps(location, sort_keys=True, default=str)).encode()
        ).hexdigest()
        identifier = uuid5(self.blob.version_id, f"{self.version}/{len(self.items)}/{identity}")
        self.items.append(
            ParsedBlock(
                org_id=self.blob.org_id,
                kg_id=self.blob.kg_id,
                material_id=self.blob.material_id,
                version_id=self.blob.version_id,
                id=identifier,
                text=text,
                location=SourceLocation.model_validate(
                    {"kind": kind, "block_id": identifier, **location}
                ),
            )
        )

    def document(self) -> ParsedDocument:
        return ParsedDocument(
            blob=self.blob,
            blocks=tuple(self.items),
            parser_version=self.version,
            warnings=tuple(dict.fromkeys(self.warnings)),
            images=tuple(self.images),
        )


def docx(data: bytes, blocks: Blocks) -> None:
    from domain_kg.ingestion.archives import office_archive

    with office_archive(data, blocks.options) as archive:
        if (
            "word/document.xml" not in archive.namelist()
            or "[Content_Types].xml" not in archive.namelist()
        ):
            raise DomainError(ErrorCode.UNSUPPORTED_FORMAT, "This is not a DOCX document.")
        limit = min(16 * 1024 * 1024, blocks.options.max_expanded_bytes)
        with archive.open("word/document.xml") as stream:
            document = stream.read(limit + 1)
        if len(document) > limit:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "DOCX text exceeds the parser limit.")
    root = fromstring(document, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    body = root.find(f"{WORD}body")
    if body is None:
        raise DomainError(ErrorCode.UNSUPPORTED_FORMAT, "DOCX has no document body.")
    paragraph, table, heading = 0, 0, None

    def text(element: Any) -> str:
        output = []
        for item in element.iter():
            if item.tag == f"{WORD}t":
                output.append(item.text or "")
            elif item.tag == f"{WORD}tab":
                output.append("\t")
            elif item.tag in (f"{WORD}br", f"{WORD}cr"):
                output.append("\n")
        return "".join(output)

    for item in body:
        if item.tag == f"{WORD}p":
            value = text(item)
            style = item.find(f"{WORD}pPr/{WORD}pStyle")
            if style is not None and style.get(f"{WORD}val", "").lower().startswith("heading"):
                heading = value[:4000]
            blocks.add(value, "docx", paragraph=paragraph, heading=heading)
            paragraph += 1
        elif item.tag == f"{WORD}tbl":
            for row, entry in enumerate(item.findall(f"{WORD}tr")):
                blocks.add(
                    " | ".join(text(cell) for cell in entry.findall(f"{WORD}tc")),
                    "docx",
                    table=table,
                    row=row,
                    heading=heading,
                )
            table += 1


class InertHTML(HTMLParser):
    def __init__(self, blocks: Blocks) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks, self.hidden = blocks, 0
        self.buffer: list[str] = []
        self.heading: str | None = None
        self.in_heading = False

    def flush(self) -> None:
        value = "".join(self.buffer)
        if self.in_heading and value:
            self.heading = value[:4000]
        self.blocks.add(value, "html", heading=self.heading)
        self.buffer = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "iframe", "object", "template", "noscript"}:
            self.hidden += 1
        if not self.hidden and tag in {"p", "div", "li", "tr", "br", "h1", "h2", "h3", "h4"}:
            self.flush()
            self.in_heading = tag.startswith("h")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "iframe", "object", "template", "noscript"}:
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.flush()
            self.in_heading = False

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.buffer.append(data)


def decode(data: bytes, blob: BlobRef, options: ParseOptions) -> ParsedDocument:
    parser_version = (
        PDF_VERSION
        if blob.media_type == "application/pdf"
        else OFFICE_VERSION
        if blob.media_type in (XLSX, PPTX, ODP, *LEGACY)
        else "deterministic-text-xml-v1"
    )
    blocks = Blocks(blob, options, parser_version)
    decoded = ""
    if blob.media_type in (
        "text/plain",
        "text/markdown",
        "text/html",
        "text/csv",
        "text/tab-separated-values",
        "application/json",
    ):
        from domain_kg.ingestion.text import decode_text

        decoded, warning = decode_text(data)
        if warning:
            blocks.warnings.append(warning)
    media_type = blob.media_type
    if media_type in LEGACY and media_type not in (XLS, ODS):
        from domain_kg.ingestion.conversion import normalize

        source_ext, target_ext, media_type = LEGACY[media_type]
        data = normalize(data, source_ext, target_ext, options)
        blocks.warnings.append("OFFICE_CONVERTED")
    if media_type == "application/pdf":
        parse_pdf(data, blocks)
    elif media_type == DOCX:
        docx(data, blocks)
        from domain_kg.ingestion.images import office_images

        office_images(data, blocks, "word/media/")
    elif media_type in (XLSX, PPTX, XLS, ODS, ODP):
        from domain_kg.ingestion import office

        {XLSX: office.xlsx, PPTX: office.pptx, XLS: office.xls, ODS: office.ods, ODP: office.odp}[
            media_type
        ](data, blocks)
        if media_type in (XLSX, ODS):
            from domain_kg.ingestion.images import office_images

            office_images(data, blocks, "xl/media/" if media_type == XLSX else "Pictures/")
    elif media_type in IMAGE_TYPES:
        from domain_kg.ingestion.images import capture, image_ocr

        image = capture(blocks, data, ImageOrigin(part="original"))
        if image.media_type != media_type:
            raise DomainError(
                ErrorCode.UNSUPPORTED_FORMAT, "Image bytes do not match the declared type."
            )
        image_ocr(blocks, image)
    elif blob.media_type in ("text/plain", "text/markdown"):
        text = decoded
        heading = None
        for index, line in enumerate(text.splitlines(keepends=True)):
            if blob.media_type == "text/markdown" and line.lstrip().startswith("#"):
                heading = line.strip().lstrip("#").strip()[:4000]
            blocks.add(line, "text", paragraph=index, heading=heading)
    elif blob.media_type == "text/html":
        html = InertHTML(blocks)
        html.feed(decoded)
        html.close()
        html.flush()
    elif blob.media_type in ("text/csv", "text/tab-separated-values"):
        csv.field_size_limit(min(options.max_expanded_bytes, 1024 * 1024))
        for number, row in enumerate(
            csv.reader(
                io.StringIO(decoded, newline=""),
                delimiter="\t" if blob.media_type == "text/tab-separated-values" else ",",
            ),
            1,
        ):
            blocks.add(" | ".join(row), "csv", row=number)
    elif blob.media_type == "application/json":

        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError("JSON object keys must be unique")
                result[key] = item
            return result

        def visit(value: Any, pointer: str, depth: int) -> None:
            if depth > 50 or len(pointer) > 4000:
                raise DomainError(
                    ErrorCode.QUOTA_EXCEEDED, "JSON nesting or pointer limit exceeded."
                )
            if isinstance(value, dict):
                for key, item in value.items():
                    visit(
                        item, pointer + "/" + key.replace("~", "~0").replace("/", "~1"), depth + 1
                    )
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    visit(item, pointer + f"/{index}", depth + 1)
            else:
                blocks.add(
                    value if isinstance(value, str) else json.dumps(value, allow_nan=False),
                    "json",
                    json_pointer=pointer,
                )

        def invalid_constant(value: str) -> None:
            raise ValueError("JSON constants must be finite")

        visit(
            json.loads(decoded, parse_constant=invalid_constant, object_pairs_hook=unique_object),
            "",
            0,
        )
    else:
        raise DomainError(
            ErrorCode.UNSUPPORTED_FORMAT, "Export this document as PDF, DOCX or UTF-8 text."
        )
    return blocks.document()
