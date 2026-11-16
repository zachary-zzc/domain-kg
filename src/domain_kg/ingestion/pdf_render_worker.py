"""One bounded PDF raster per child; inherits the parser's offline sandbox."""

import io
import sys
from pathlib import Path
from typing import Annotated

from pydantic import Field

from domain_kg.models import FrozenModel

Coordinate = Annotated[int, Field(ge=0, le=4096, strict=True)]
Side = Annotated[int, Field(ge=1, le=4096, strict=True)]


class RenderRequest(FrozenModel):
    page_number: Annotated[int, Field(ge=1, le=500, strict=True)]
    canvas: tuple[Side, Side]
    masks: Annotated[
        tuple[tuple[Coordinate, Coordinate, Coordinate, Coordinate], ...], Field(max_length=100000)
    ]


def render(data: bytes, request: RenderRequest, output: Path) -> None:
    from docling_parse.pdf_parser import (
        ContentConfig,
        ContentLevel,
        DecodeConfig,
        DoclingThreadedPdfParser,
        ThreadedPdfParserConfig,
    )
    from docling_parse.pdf_parsers import RenderConfig

    config = RenderConfig()
    config.scale = -1.0
    config.canvas_width, config.canvas_height = request.canvas
    config.render_text = False
    renderer = DoclingThreadedPdfParser(
        parser_config=ThreadedPdfParserConfig(
            threads=1,
            max_concurrent_results=1,
            render_config=config,
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
    key = renderer.load(io.BytesIO(data), page_numbers=[request.page_number])
    try:
        result = next(renderer.iterate_results())
        if not result.success or result.page_number != request.page_number:
            raise ValueError("PDF rendering failed")
        with result.get_image() as image:
            for left, top, right, bottom in request.masks:
                image.paste("white", (left, top, right, bottom))
            image.save(output, format="PNG")
    finally:
        renderer.unload(key)


def main() -> None:
    config_path, output_path = sys.argv[1:]
    request = RenderRequest.model_validate_json(Path(config_path).read_bytes())
    if request.canvas[0] * request.canvas[1] > 8_000_000:
        raise ValueError("PDF rendering pixel limit exceeded")
    data = sys.stdin.buffer.read(50 * 1024 * 1024 + 1)
    if len(data) > 50 * 1024 * 1024:
        raise ValueError("PDF rendering original limit exceeded")
    render(data, request, Path(output_path))


if __name__ == "__main__":
    main()
