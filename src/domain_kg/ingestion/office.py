"""Bounded Office readers; formulas and external links remain inert source data."""

import io
from datetime import date, datetime, time
from typing import TYPE_CHECKING

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.ingestion.archives import office_archive

if TYPE_CHECKING:
    from pptx.shapes.base import BaseShape

    from domain_kg.ingestion.formats import Blocks


def xlsx(data: bytes, blocks: "Blocks") -> None:
    from openpyxl import load_workbook

    with office_archive(data, blocks.options) as archive:
        if "xl/workbook.xml" not in archive.namelist():
            raise DomainError(ErrorCode.UNSUPPORTED_FORMAT, "This is not an XLSX workbook.")
    formulas = load_workbook(io.BytesIO(data), read_only=True, data_only=False, keep_links=False)
    cached = load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
    try:
        cells = 0
        for sheet, values in zip(formulas.worksheets, cached.worksheets, strict=True):
            if len(formulas.worksheets) > 500:
                raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Workbook exceeds the worksheet limit.")
            if (sheet.max_row or 0) * (sheet.max_column or 0) > 1_000_000:
                raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Workbook exceeds the cell limit.")
            for row_number, (row, cached_row) in enumerate(
                zip(sheet.rows, values.rows, strict=True), 1
            ):
                rendered: list[str] = []
                for cell, cached_cell in zip(row, cached_row, strict=True):
                    cells += 1
                    if cells > 1_000_000:
                        raise DomainError(
                            ErrorCode.QUOTA_EXCEEDED, "Workbook exceeds the cell limit."
                        )
                    value = cell.value
                    if value is None:
                        continue
                    text = (
                        value.isoformat()
                        if isinstance(value, (date, datetime, time))
                        else str(value)
                    )
                    if cell.data_type == "f" and cached_cell.value is not None:
                        text += " [cached result: " + str(cached_cell.value) + "]"
                    rendered.append(str(cell.coordinate) + ": " + text)
                blocks.add(" | ".join(rendered), "xlsx", sheet=sheet.title, row=row_number)
    finally:
        formulas.close()
        cached.close()


def xls(data: bytes, blocks: "Blocks") -> None:
    import xlrd

    workbook = xlrd.open_workbook(file_contents=data, on_demand=True)
    try:
        cells = 0
        if workbook.nsheets > 500:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Workbook exceeds the worksheet limit.")
        for name in workbook.sheet_names():
            sheet = workbook.sheet_by_name(name)
            cells += sheet.nrows * sheet.ncols
            if cells > 1_000_000:
                raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Workbook exceeds the cell limit.")
            for row_number in range(sheet.nrows):
                values = []
                for column in range(sheet.ncols):
                    cell = sheet.cell(row_number, column)
                    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                        continue
                    value = cell.value
                    if cell.ctype == xlrd.XL_CELL_DATE and isinstance(value, (float, int)):
                        value = xlrd.xldate_as_datetime(value, workbook.datemode).isoformat()
                    from openpyxl.utils import get_column_letter

                    values.append(f"{get_column_letter(column + 1)}{row_number + 1}: {value}")
                blocks.add(" | ".join(values), "xlsx", sheet=name, row=row_number + 1)
            workbook.unload_sheet(name)
    finally:
        workbook.release_resources()


def ods(data: bytes, blocks: "Blocks") -> None:
    from defusedxml.ElementTree import fromstring
    from openpyxl.utils import get_column_letter

    ns = {
        "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
        "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
        "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    }
    table_ns, office_ns = "{" + ns["table"] + "}", "{" + ns["office"] + "}"
    with office_archive(data, blocks.options) as archive:
        if "content.xml" not in archive.namelist():
            raise DomainError(ErrorCode.UNSUPPORTED_FORMAT, "This is not an ODS spreadsheet.")
        root = fromstring(
            archive.read("content.xml"), forbid_dtd=True, forbid_entities=True, forbid_external=True
        )
    sheets = root.findall("office:body/office:spreadsheet/table:table", ns)
    if len(sheets) > 500:
        raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Workbook exceeds the worksheet limit.")
    for sheet in sheets:
        name, row_number = sheet.get(table_ns + "name", "Sheet"), 1
        for row in sheet.iter(table_ns + "table-row"):
            repeat = int(row.get(table_ns + "number-rows-repeated", "1"))
            column = 1
            values: list[tuple[str, str]] = []
            for cell in row:
                count = int(cell.get(table_ns + "number-columns-repeated", "1"))
                paragraphs = cell.findall("text:p", ns)
                text = "\n".join("".join(p.itertext()) for p in paragraphs)
                text = text or cell.get(office_ns + "value", cell.get(office_ns + "date-value", ""))
                formula = cell.get(table_ns + "formula")
                if formula:
                    text = formula + (" [cached result: " + text + "]" if text else "")
                if (
                    count < 1
                    or repeat < 1
                    or column + count > 16385
                    or row_number + repeat > 1_048_577
                ):
                    raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Workbook exceeds the cell limit.")
                if text:
                    if count * repeat > 100000:
                        raise DomainError(
                            ErrorCode.QUOTA_EXCEEDED, "Workbook exceeds the cell limit."
                        )
                    values.extend(
                        (get_column_letter(column + offset), text) for offset in range(count)
                    )
                column += count
            if values:
                for offset in range(repeat):
                    number = row_number + offset
                    blocks.add(
                        " | ".join(f"{col}{number}: {text}" for col, text in values),
                        "xlsx",
                        sheet=name,
                        row=number,
                    )
            row_number += repeat


def pptx(data: bytes, blocks: "Blocks") -> None:
    from pptx import Presentation
    from pptx.shapes.autoshape import Shape
    from pptx.shapes.graphfrm import GraphicFrame
    from pptx.shapes.group import GroupShape
    from pptx.shapes.picture import Picture

    from domain_kg.ingestion.images import embedded
    from domain_kg.ingestion.models import ImageOrigin

    with office_archive(data, blocks.options) as archive:
        if "ppt/presentation.xml" not in archive.namelist():
            raise DomainError(ErrorCode.UNSUPPORTED_FORMAT, "This is not a PPTX presentation.")
    presentation = Presentation(io.BytesIO(data))
    if len(presentation.slides) > blocks.options.max_pages:
        raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Presentation exceeds the slide limit.")
    count = 0

    def shape_text(shape: "BaseShape", slide: int, heading: str | None) -> None:
        nonlocal count
        count += 1
        if count > 100000:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Presentation exceeds the shape limit.")
        if isinstance(shape, GroupShape):
            for child in shape.shapes:
                shape_text(child, slide, heading)
        elif isinstance(shape, Picture):
            embedded(
                blocks, shape.image.blob, ImageOrigin(part=str(shape.image.filename), slide=slide)
            )
        elif isinstance(shape, GraphicFrame) and shape.has_table:
            for index, row in enumerate(shape.table.rows):
                blocks.add(
                    " | ".join(cell.text for cell in row.cells),
                    "pptx",
                    slide=slide,
                    table=shape.shape_id,
                    row=index,
                    heading=heading,
                )
        elif isinstance(shape, Shape) and shape.has_text_frame:
            for index, paragraph in enumerate(shape.text_frame.paragraphs):
                blocks.add(paragraph.text, "pptx", slide=slide, paragraph=index, heading=heading)

    for number, slide in enumerate(presentation.slides, 1):
        heading = slide.shapes.title.text if slide.shapes.title else None
        for shape in slide.shapes:
            shape_text(shape, number, heading)
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame
            if notes is not None:
                for index, paragraph in enumerate(notes.paragraphs):
                    blocks.add(
                        paragraph.text, "pptx", slide=number, paragraph=index, section="notes"
                    )


def odp(data: bytes, blocks: "Blocks") -> None:
    from xml.etree.ElementTree import Element

    from defusedxml.ElementTree import fromstring

    from domain_kg.ingestion.images import embedded
    from domain_kg.ingestion.models import ImageOrigin

    ns = {
        name: f"urn:oasis:names:tc:opendocument:xmlns:{name}:1.0"
        for name in ("office", "drawing", "presentation", "text", "table")
    }
    draw, text_ns, table_ns = (
        "{" + ns["drawing"] + "}",
        "{" + ns["text"] + "}",
        "{" + ns["table"] + "}",
    )
    with office_archive(data, blocks.options) as archive:
        root = fromstring(
            archive.read("content.xml"), forbid_dtd=True, forbid_entities=True, forbid_external=True
        )
        slides = root.findall("office:body/office:presentation/drawing:page", ns)
        if not slides or len(slides) > blocks.options.max_pages:
            raise DomainError(
                ErrorCode.UNSUPPORTED_FORMAT, "ODP has no slides or exceeds the slide limit."
            )
        table_number = 0
        paragraph = 0

        def walk(element: Element, slide: int, heading: str, notes: bool = False) -> None:
            nonlocal table_number, paragraph
            section = "notes" if notes else None
            if element.tag in (text_ns + "p", text_ns + "h"):
                blocks.add(
                    "".join(element.itertext()),
                    "pptx",
                    slide=slide,
                    heading=heading,
                    paragraph=paragraph,
                    section=section,
                )
                paragraph += 1
            elif element.tag == table_ns + "table":
                for index, row in enumerate(element.iter(table_ns + "table-row")):
                    values: list[str] = []
                    for cell in row:
                        repeat = int(cell.get(table_ns + "number-columns-repeated", "1"))
                        if repeat < 1 or len(values) + repeat > 16384:
                            raise DomainError(
                                ErrorCode.QUOTA_EXCEEDED,
                                "Presentation table exceeds the cell limit.",
                            )
                        value = "\n".join("".join(p.itertext()) for p in cell.findall("text:p", ns))
                        values.extend([value] * repeat)
                    blocks.add(
                        " | ".join(values),
                        "pptx",
                        slide=slide,
                        heading=heading,
                        table=table_number,
                        row=index,
                        section=section,
                    )
                table_number += 1
            elif element.tag == draw + "image":
                from urllib.parse import unquote

                part = unquote(element.get("{http://www.w3.org/1999/xlink}href", ""))
                if part and part in archive.namelist():
                    embedded(blocks, archive.read(part), ImageOrigin(part=part, slide=slide))
                else:
                    blocks.warnings.append("EMBEDDED_IMAGE_UNSUPPORTED")
            else:
                for child in element:
                    walk(
                        child,
                        slide,
                        heading,
                        notes or element.tag == "{" + ns["presentation"] + "}notes",
                    )

        for number, slide in enumerate(slides, 1):
            walk(slide, number, slide.get(draw + "name", ""))
