"""One offline PDF adapter: lightweight native text and bounded local scan OCR."""

import io
import math
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from pypdf import PageObject, PdfReader
from pypdf.errors import PdfReadError
from pypdf.generic import DictionaryObject, PdfObject

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.ingestion.pdf_render_worker import RenderRequest

if TYPE_CHECKING:
    from domain_kg.ingestion.formats import Blocks

VERSION = (
    f"local-pdf-v4/docling-parse/{version('docling-parse')}/pypdf/{version('pypdf')}"
    "/tesseract-eng-chi_sim-v1"
)
MAX_OCR_PIXELS = 8_000_000
MAX_OCR_SIDE = 4096


@dataclass(frozen=True)
class NativePage:
    page_number: int
    width: float
    height: float
    text: tuple[str, ...]
    masks: tuple[tuple[float, float, float, float], ...]
    has_bitmaps: bool
    bitmap_area: float


def native_page(page: PageObject, page_number: int) -> NativePage:
    """Read native text without materializing unused glyph/word/shape geometry."""
    if page.rotation:
        page.transfer_rotation_to_content()
    width, height = float(page.cropbox.width), float(page.cropbox.height)
    left_edge, bottom_edge = float(page.cropbox.left), float(page.cropbox.bottom)
    if (
        not all(math.isfinite(value) for value in (width, height, left_edge, bottom_edge))
        or min(width, height) <= 0
    ):
        raise DomainError(ErrorCode.UNSUPPORTED_FORMAT, "Invalid PDF page dimensions.")
    masks: list[tuple[float, float, float, float]] = []
    has_bitmaps, bitmap_area = False, 0.0

    def rectangle(
        matrix: list[float], x: float, y: float, w: float, h: float
    ) -> tuple[float, float, float, float]:
        points = [
            (
                matrix[0] * px + matrix[2] * py + matrix[4],
                matrix[1] * px + matrix[3] * py + matrix[5],
            )
            for px, py in ((x, y), (x + w, y), (x, y + h), (x + w, y + h))
        ]
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        if not all(math.isfinite(value) for value in (*xs, *ys)):
            raise DomainError(ErrorCode.UNSUPPORTED_FORMAT, "Invalid PDF page geometry.")
        return (
            max(0.0, min(xs) - left_edge),
            max(0.0, height - max(ys) + bottom_edge),
            min(width, max(xs) - left_edge),
            min(height, height - min(ys) + bottom_edge),
        )

    def text_geometry(
        text: str,
        cm: list[float],
        tm: list[float],
        font: DictionaryObject | None,
        size: float,
    ) -> None:
        if not text.strip():
            return
        # Native glyphs are disabled in the rendered image. Also mask their
        # approximate source regions when a scan has an overlaid text layer.
        matrix = [
            tm[0] * cm[0] + tm[1] * cm[2],
            tm[0] * cm[1] + tm[1] * cm[3],
            tm[2] * cm[0] + tm[3] * cm[2],
            tm[2] * cm[1] + tm[3] * cm[3],
            tm[4] * cm[0] + tm[5] * cm[2] + cm[4],
            tm[4] * cm[1] + tm[5] * cm[3] + cm[5],
        ]
        lines = text.splitlines()
        masks.append(rectangle(matrix, 0, -size * 0.25, max(map(len, lines)) * size, size * 1.5))

    def image_geometry(
        operator: bytes, operands: list[PdfObject], cm: list[float], tm: list[float]
    ) -> None:
        nonlocal has_bitmaps, bitmap_area
        if operator == b"Do" and operands:
            try:
                resources = page.get("/Resources")
                resources = resources.get_object() if resources is not None else None
                if not isinstance(resources, DictionaryObject):
                    return
                objects = resources.get("/XObject")
                objects = objects.get_object() if objects is not None else None
                if not isinstance(objects, DictionaryObject):
                    return
                image = objects[operands[0]].get_object()
                if not isinstance(image, DictionaryObject) or image.get("/Subtype") != "/Image":
                    return
            except (KeyError, PdfReadError):
                return  # The image-source pass records a warning for broken objects.
        elif operator != b"INLINE IMAGE":
            return
        has_bitmaps = True
        left, top, right, bottom = rectangle(cm, 0, 0, 1, 1)
        bitmap_area += max(0.0, right - left) * max(0.0, bottom - top)

    text = page.extract_text(visitor_text=text_geometry, visitor_operand_before=image_geometry)
    return NativePage(
        page_number=page_number,
        width=width,
        height=height,
        text=tuple(line for line in text.splitlines() if line),
        masks=tuple(masks),
        has_bitmaps=has_bitmaps,
        bitmap_area=bitmap_area,
    )


def recognize(data: bytes, page: NativePage) -> str:
    # Search installation locations, never a document/client-selected executable.
    engine = shutil.which("tesseract", path="/usr/bin:/opt/homebrew/bin:/usr/local/bin")
    if engine is None:
        raise DomainError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "Local PDF OCR requires Tesseract with English and simplified Chinese language data.",
        )
    width, height = page.width, page.height
    if not all(math.isfinite(value) and value > 0 for value in (width, height)):
        raise DomainError(ErrorCode.UNSUPPORTED_FORMAT, "Invalid PDF page dimensions.")
    scale = min(3.0, MAX_OCR_SIDE / max(width, height), math.sqrt(MAX_OCR_PIXELS / width / height))
    canvas = (max(1, int(width * scale)), max(1, int(height * scale)))
    masks: list[tuple[int, int, int, int]] = []
    for left_edge, top_edge, right_edge, bottom_edge in page.masks:
        left, top = (
            max(0, int(left_edge * canvas[0] / width) - 1),
            max(0, int(top_edge * canvas[1] / height) - 1),
        )
        right, bottom = (
            min(canvas[0], math.ceil(right_edge * canvas[0] / width) + 1),
            min(canvas[1], math.ceil(bottom_edge * canvas[1] / height) + 1),
        )
        if right > left and bottom > top:
            masks.append((left, top, right, bottom))
    request = RenderRequest(page_number=page.page_number, canvas=canvas, masks=tuple(masks))
    # Renderer buffers are native allocations that are not reliably returned by
    # Python GC. Exit the rendering child before OCR starts; both children stay
    # in the parser's offline sandbox and monitored process group.
    with TemporaryDirectory(prefix="pdf-render-", dir=os.environ.get("TMPDIR")) as temporary:
        directory = Path(temporary)
        config, output = directory / "request.json", directory / "page.png"
        config.write_text(request.model_dump_json())
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "domain_kg.ingestion.pdf_render_worker",
                    str(config),
                    str(output),
                ],
                input=data,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise DomainError(
                ErrorCode.QUOTA_EXCEEDED, "Local PDF rendering runtime limit exceeded."
            ) from exc
        except OSError as exc:
            raise DomainError(
                ErrorCode.PROVIDER_UNAVAILABLE, "Local PDF OCR could not render a page."
            ) from exc
        if result.returncode != 0 or not output.is_file():
            raise DomainError(
                ErrorCode.UNSUPPORTED_FORMAT, "Local PDF OCR could not render a page."
            )
        if output.stat().st_size > 32 * 1024 * 1024:
            raise DomainError(
                ErrorCode.QUOTA_EXCEEDED, "Local PDF rendering output limit exceeded."
            )
        raster = output.read_bytes()
    from domain_kg.ingestion.ocr import recognize_raster

    return recognize_raster(raster, engine=engine)


def parse_pdf(data: bytes, blocks: "Blocks") -> None:
    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted:
        raise DomainError(
            ErrorCode.UNSUPPORTED_FORMAT, "Export an unencrypted PDF before uploading."
        )
    if len(reader.pages) > blocks.options.max_pages:
        raise DomainError(ErrorCode.QUOTA_EXCEEDED, "PDF exceeds the configured page limit.")
    try:
        for page_number, original_page in enumerate(reader.pages, 1):
            page = native_page(original_page, page_number)
            from domain_kg.ingestion.images import embedded
            from domain_kg.ingestion.models import ImageOrigin

            try:
                for image in reader.pages[page.page_number - 1].images:
                    try:
                        embedded(
                            blocks, image.data, ImageOrigin(part=image.name, page=page.page_number)
                        )
                    finally:
                        if image.image is not None:
                            image.image.close()
            except PdfReadError:
                # A missing optional image object must not discard readable
                # native/OCR evidence. The retained original still shows damage.
                blocks.warnings.append("PDF_IMAGE_UNREADABLE")
            for text in page.text:
                blocks.add(text, "pdf", page=page.page_number)
            # Scan bodies can coexist with a native header. Small logos do not
            # justify another OCR pass on an otherwise native page.
            page_has_images = any(
                origin.page == page.page_number
                for image in blocks.images
                for origin in image.origins
            )
            needs_ocr = (not page.text and page_has_images) or (
                page.has_bitmaps and page.bitmap_area >= page.width * page.height * 0.5
            )
            if needs_ocr:
                if blocks.options.ocr:
                    text = recognize(data, page)
                    if text.strip():
                        blocks.add(text, "pdf", page=page.page_number, text_origin="ocr")
                        blocks.warnings.append("OCR_APPLIED")
                    else:
                        blocks.warnings.append("OCR_NO_TEXT")
                else:
                    blocks.warnings.append("OCR_REQUIRED")
            elif not page.text:
                blocks.warnings.append("NO_EXTRACTABLE_TEXT")
    finally:
        reader.close()
