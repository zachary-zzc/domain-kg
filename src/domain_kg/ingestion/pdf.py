"""One offline PDF adapter: validation, native text, and bounded local scan OCR."""

import io
import math
import shutil
from importlib.metadata import version
from typing import TYPE_CHECKING

from pypdf import PdfReader

from domain_kg.errors import DomainError, ErrorCode

if TYPE_CHECKING:
    from docling_core.types.doc.page import SegmentedPdfPage

    from domain_kg.ingestion.formats import Blocks

VERSION = (
    f"local-pdf-v3/docling-parse/{version('docling-parse')}/pypdf/{version('pypdf')}"
    "/tesseract-eng-chi_sim-v1"
)
MAX_OCR_PIXELS = 8_000_000
MAX_OCR_SIDE = 4096
MAX_OCR_OUTPUT = 1024 * 1024


def recognize(data: bytes, page_number: int, page: "SegmentedPdfPage") -> str:
    # Search installation locations, never a document/client-selected executable.
    engine = shutil.which("tesseract", path="/usr/bin:/opt/homebrew/bin:/usr/local/bin")
    if engine is None:
        raise DomainError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "Local PDF OCR requires Tesseract with English and simplified Chinese language data.",
        )
    from docling_parse.pdf_parser import (
        ContentConfig,
        ContentLevel,
        DecodeConfig,
        DoclingThreadedPdfParser,
        ThreadedPdfParserConfig,
    )
    from docling_parse.pdf_parsers import RenderConfig

    width, height = float(page.dimension.width), float(page.dimension.height)
    if not all(math.isfinite(value) and value > 0 for value in (width, height)):
        raise DomainError(ErrorCode.UNSUPPORTED_FORMAT, "Invalid PDF page dimensions.")
    scale = min(3.0, MAX_OCR_SIDE / max(width, height), math.sqrt(MAX_OCR_PIXELS / width / height))
    canvas = (max(1, int(width * scale)), max(1, int(height * scale)))
    render = RenderConfig()
    render.scale = -1.0
    render.canvas_width, render.canvas_height = canvas
    render.render_text = False
    renderer = DoclingThreadedPdfParser(
        parser_config=ThreadedPdfParserConfig(
            threads=1,
            max_concurrent_results=1,
            render_config=render,
            page_content_config=ContentConfig(
                char_cells_content_level=ContentLevel.SKIP,
                word_cells_content_level=ContentLevel.SKIP,
                line_cells_content_level=ContentLevel.SKIP,
                shapes_content_level=ContentLevel.COMPUTE,
                bitmaps_content_level=ContentLevel.COMPUTE,
            ),
        ),
        decode_config=DecodeConfig(keep_glyphs=False),
    )
    key = renderer.load(io.BytesIO(data), page_numbers=[page_number])
    try:
        result = next(renderer.iterate_results())
        if not result.success or result.page_number != page_number:
            raise DomainError(
                ErrorCode.UNSUPPORTED_FORMAT, "Local PDF OCR could not render a page."
            )
        with result.get_image() as image:
            # Native cells may overlay an existing scan. Mask those regions so
            # their exact native text is retained once, rather than re-recognized.
            for cell in page.textline_cells:
                if not cell.text:
                    continue
                box = cell.rect.to_bounding_box().to_top_left_origin(page_height=height)
                left, top = (
                    max(0, int(box.l * canvas[0] / width) - 1),
                    max(0, int(box.t * canvas[1] / height) - 1),
                )
                right, bottom = (
                    min(canvas[0], math.ceil(box.r * canvas[0] / width) + 1),
                    min(canvas[1], math.ceil(box.b * canvas[1] / height) + 1),
                )
                if right > left and bottom > top:
                    image.paste("white", (left, top, right, bottom))
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
    finally:
        renderer.unload(key)
    from domain_kg.ingestion.ocr import recognize_raster

    return recognize_raster(buffer.getvalue(), engine=engine)


def parse_pdf(data: bytes, blocks: "Blocks") -> None:
    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted:
        raise DomainError(
            ErrorCode.UNSUPPORTED_FORMAT, "Export an unencrypted PDF before uploading."
        )
    if len(reader.pages) > blocks.options.max_pages:
        raise DomainError(ErrorCode.QUOTA_EXCEEDED, "PDF exceeds the 500-page parser limit.")
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
            bitmaps_content_level=ContentLevel.COMPUTE_AND_MATERIALIZE,
            include_bitmap_bytes=False,
        ),
    )
    try:
        for page_number, page in document.iterate_pages():
            from domain_kg.ingestion.images import embedded
            from domain_kg.ingestion.models import ImageOrigin

            for image in reader.pages[page_number - 1].images:
                embedded(blocks, image.data, ImageOrigin(part=image.name, page=page_number))
            native = [cell for cell in page.textline_cells if cell.text]
            for cell in native:
                blocks.add(cell.text, "pdf", page=page_number)
            # Scan bodies can coexist with a short native header. Small logos on
            # otherwise native pages do not justify a second OCR pass.
            image_area = sum(
                float(image.rect.width * image.rect.height) for image in page.bitmap_resources
            )
            needs_ocr = bool(page.bitmap_resources) and (
                not native
                or image_area >= float(page.dimension.width * page.dimension.height) * 0.5
            )
            if needs_ocr:
                if blocks.options.ocr:
                    text = recognize(data, page_number, page)
                    if text.strip():
                        blocks.add(text, "pdf", page=page_number, text_origin="ocr")
                        blocks.warnings.append("OCR_APPLIED")
                    else:
                        blocks.warnings.append("OCR_NO_TEXT")
                else:
                    blocks.warnings.append("OCR_REQUIRED")
            elif not native:
                blocks.warnings.append("NO_EXTRACTABLE_TEXT")
            document.unload_pages((page_number, page_number + 1))
    finally:
        document.unload()
