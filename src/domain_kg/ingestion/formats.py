"""Deterministic offline format decoders, invoked only in the bounded parser process."""

import csv
import hashlib
import io
import json
import stat
from html.parser import HTMLParser
from importlib.metadata import version
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid5
from zipfile import BadZipFile, ZipFile

from defusedxml.ElementTree import fromstring
from pypdf import PdfReader

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.ingestion.models import ParsedBlock, ParsedDocument, ParseOptions, SourceLocation
from domain_kg.materials.models import BlobRef

WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class Blocks:
    def __init__(self, blob: BlobRef, options: ParseOptions, parser_version: str) -> None:
        self.blob, self.options, self.version = blob, options, parser_version
        self.items: list[ParsedBlock] = []
        self.bytes = 0
        self.warnings: list[str] = []

    def add(self, text: str, kind: str, **location: Any) -> None:
        if not text:
            return
        self.bytes += len(text.encode()) + len(str(location.get("heading", "")).encode())
        if self.bytes > self.options.max_expanded_bytes or len(self.items) >= 100000:
            raise DomainError(
                ErrorCode.QUOTA_EXCEEDED, "Expanded document exceeds the parser limit."
            )
        identity = hashlib.sha256(
            (text + json.dumps(location, sort_keys=True)).encode()
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
        )


def pdf(data: bytes, blocks: Blocks) -> None:
    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted:
        raise DomainError(
            ErrorCode.UNSUPPORTED_FORMAT, "Export an unencrypted PDF before uploading."
        )
    if len(reader.pages) > blocks.options.max_pages:
        raise DomainError(ErrorCode.QUOTA_EXCEEDED, "PDF exceeds the 500-page parser limit.")
    if blocks.options.ocr:
        raise DomainError(
            ErrorCode.PROVIDER_UNAVAILABLE, "OCR needs a configured approved parser profile."
        )
    from docling_parse.pdf_parser import ContentConfig, ContentLevel, DecodeConfig, DoclingPdfParser

    parser = DoclingPdfParser(loglevel="fatal")
    document = parser.load(
        io.BytesIO(data),
        decode_config=DecodeConfig(keep_glyphs=False),
        content_config=ContentConfig(
            char_cells_content_level=ContentLevel.SKIP,
            word_cells_content_level=ContentLevel.COMPUTE,
            line_cells_content_level=ContentLevel.COMPUTE_AND_MATERIALIZE,
            shapes_content_level=ContentLevel.SKIP,
            bitmaps_content_level=ContentLevel.SKIP,
        ),
    )
    try:
        for page_number, page in document.iterate_pages():
            found = False
            for cell in page.textline_cells:
                if cell.text:
                    blocks.add(cell.text, "pdf", page=page_number)
                    found = True
            if not found:
                resources = reader.pages[page_number - 1].get("/Resources", {})
                objects = resources.get("/XObject", {}) if resources else {}
                image = any(
                    value.get_object().get("/Subtype") == "/Image" for value in objects.values()
                )
                blocks.warnings.append("OCR_REQUIRED" if image else "NO_EXTRACTABLE_TEXT")
            document.unload_pages((page_number, page_number + 1))
    finally:
        document.unload()


def docx(data: bytes, blocks: Blocks) -> None:
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if (
                len(members) > 2048
                or sum(item.file_size for item in members) > blocks.options.max_expanded_bytes
            ):
                raise DomainError(
                    ErrorCode.QUOTA_EXCEEDED, "Expanded archive exceeds the parser limit."
                )
            names = [item.filename for item in members]
            if len(names) != len(set(names)):
                raise DomainError(ErrorCode.UNSUPPORTED_FORMAT, "Archive entries must be unique.")
            for item in members:
                path = PurePosixPath(item.filename)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in item.filename
                    or ":" in item.filename
                    or stat.S_ISLNK(item.external_attr >> 16)
                ):
                    raise DomainError(ErrorCode.UNSUPPORTED_FORMAT, "Unsafe archive entry.")
            if "word/document.xml" not in names or "[Content_Types].xml" not in names:
                raise DomainError(ErrorCode.UNSUPPORTED_FORMAT, "This is not a DOCX document.")
            limit = min(16 * 1024 * 1024, blocks.options.max_expanded_bytes)
            with archive.open("word/document.xml") as stream:
                document = stream.read(limit + 1)
            if len(document) > limit:
                raise DomainError(ErrorCode.QUOTA_EXCEEDED, "DOCX text exceeds the parser limit.")
    except BadZipFile as exc:
        raise DomainError(ErrorCode.UNSUPPORTED_FORMAT, "Malformed DOCX archive.") from exc
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
        f"docling-parse/{version('docling-parse')}-native-v1"
        if blob.media_type == "application/pdf"
        else "deterministic-text-xml-v1"
    )
    blocks = Blocks(blob, options, parser_version)
    if blob.media_type == "application/pdf":
        pdf(data, blocks)
    elif blob.media_type == DOCX:
        docx(data, blocks)
    elif blob.media_type in ("text/plain", "text/markdown"):
        text = data.decode("utf-8")
        heading = None
        for index, line in enumerate(text.splitlines(keepends=True)):
            if blob.media_type == "text/markdown" and line.lstrip().startswith("#"):
                heading = line.strip().lstrip("#").strip()[:4000]
            blocks.add(line, "text", paragraph=index, heading=heading)
    elif blob.media_type == "text/html":
        html = InertHTML(blocks)
        html.feed(data.decode("utf-8"))
        html.close()
        html.flush()
    elif blob.media_type == "text/csv":
        csv.field_size_limit(min(options.max_expanded_bytes, 1024 * 1024))
        for number, row in enumerate(csv.reader(io.StringIO(data.decode("utf-8"), newline="")), 1):
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
            json.loads(data, parse_constant=invalid_constant, object_pairs_hook=unique_object),
            "",
            0,
        )
    else:
        raise DomainError(
            ErrorCode.UNSUPPORTED_FORMAT, "Export this document as PDF, DOCX or UTF-8 text."
        )
    return blocks.document()
