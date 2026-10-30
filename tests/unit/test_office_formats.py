"""Actual Office originals retain structured evidence without executing formulas."""

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

from domain_kg.errors import DomainError
from domain_kg.ingestion.formats import decode
from domain_kg.ingestion.models import ParseOptions
from domain_kg.materials.models import BlobRef
from domain_kg.storage.blobs import BlobStore

ROOT = Path(__file__).parents[1] / "fixtures/materials"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def parse(data, mime):
    blob = BlobRef(
        org_id=uuid4(),
        kg_id=uuid4(),
        material_id=uuid4(),
        version_id=uuid4(),
        object_id=uuid4(),
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        media_type=mime,
    )
    return decode(data, blob, ParseOptions())


def test_workbook_preserves_sheet_rows_dates_hidden_text_and_formula_source():
    data = (ROOT / "workbook.xlsx").read_bytes()
    BlobStore._check_type(XLSX, data[:512])
    document = parse(data, XLSX)
    row = next(b for b in document.blocks if "Alpha uses Beta." in b.text)
    assert row.location.sheet == "实验数据" and row.location.row == 2
    assert "2026-01-02" in row.text and "=B2*2" in row.text
    assert any(
        b.location.sheet == "Hidden notes" and "Hidden source" in b.text for b in document.blocks
    )


def test_presentation_preserves_slide_table_and_presenter_notes():
    data = (ROOT / "slides.pptx").read_bytes()
    BlobStore._check_type(PPTX, data[:512])
    document = parse(data, PPTX)
    assert any(b.text == "Alpha uses Beta." and b.location.slide == 1 for b in document.blocks)
    assert any("Cell" in b.text and b.location.table is not None for b in document.blocks)
    assert any(
        "Presenter evidence." in b.text and b.location.section == "notes" for b in document.blocks
    )


def test_office_archive_rejects_entity_expansion_before_native_readers():
    import io
    from zipfile import ZipFile

    data = io.BytesIO()
    with ZipFile(data, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<!DOCTYPE x [<!ENTITY s SYSTEM "file:///etc/passwd">]><x>&s;</x>',
        )
    with pytest.raises(DomainError):
        parse(data.getvalue(), XLSX)


@pytest.mark.parametrize(
    ("name", "mime", "expected"),
    [
        ("headings-table.doc", "application/msword", "evidence paragraph"),
        ("headings-table.rtf", "application/rtf", "evidence paragraph"),
        ("headings-table.odt", "application/vnd.oasis.opendocument.text", "evidence paragraph"),
        ("workbook.xls", "application/vnd.ms-excel", "Alpha uses Beta."),
        ("workbook.ods", "application/vnd.oasis.opendocument.spreadsheet", "Alpha uses Beta."),
        ("slides.ppt", "application/vnd.ms-powerpoint", "Alpha uses Beta."),
        ("slides.odp", "application/vnd.oasis.opendocument.presentation", "Alpha uses Beta."),
    ],
)
async def test_legacy_and_open_document_formats_use_bounded_offline_parser(name, mime, expected):
    from domain_kg.ingestion.parsers import Parser

    data = (ROOT / name).read_bytes()
    BlobStore._check_type(mime, data[:512])
    blob = BlobRef(
        org_id=uuid4(),
        kg_id=uuid4(),
        material_id=uuid4(),
        version_id=uuid4(),
        object_id=uuid4(),
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        media_type=mime,
    )

    class Original:
        async def open(self, ref):
            assert ref == blob
            yield data

    document = await Parser(Original()).parse(blob, ParseOptions())
    assert expected in " ".join(b.text for b in document.blocks)


@pytest.mark.parametrize("codec", ["utf-8-sig", "utf-16", "utf-32", "gb18030"])
def test_common_unicode_and_chinese_text_encodings_preserve_exact_text(codec):
    text = "中文资料 Alpha uses Beta."
    data = text.encode(codec)
    BlobStore._check_type("text/plain", data[:512])
    document = parse(data, "text/plain")
    assert "".join(b.text for b in document.blocks) == text


def test_odp_is_read_directly_without_running_an_office_converter(monkeypatch):
    from domain_kg.ingestion import conversion

    def unexpected(*args, **kwargs):
        raise AssertionError("ODP must use its native reader")

    monkeypatch.setattr(conversion, "normalize", unexpected)
    document = parse(
        (ROOT / "slides.odp").read_bytes(), "application/vnd.oasis.opendocument.presentation"
    )
    assert any(
        "Alpha uses Beta." in block.text and block.location.slide == 1 for block in document.blocks
    )
    assert any(
        "Presenter evidence." in block.text and block.location.section == "notes"
        for block in document.blocks
    )
    assert document.images and document.images[0].origins[0].slide == 1
