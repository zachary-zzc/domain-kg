"""Local PDF OCR never hides missing prerequisites or accepts failed OCR output."""

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from domain_kg.errors import DomainError
from domain_kg.ingestion.formats import decode
from domain_kg.ingestion.models import ParseOptions, SourceLocation
from domain_kg.materials.models import BlobRef

FIXTURES = Path(__file__).parents[1] / "fixtures/materials"


def parse(name, options=None):
    data = (FIXTURES / name).read_bytes()
    blob = BlobRef(
        org_id=uuid4(),
        kg_id=uuid4(),
        material_id=uuid4(),
        version_id=uuid4(),
        object_id=uuid4(),
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        media_type="application/pdf",
    )
    return decode(data, blob, options or ParseOptions())


def test_native_pdf_needs_no_ocr_runtime_but_scan_reports_missing_engine(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda *_args, **_kwargs: None)
    native = parse("native.pdf")
    assert [block.text for block in native.blocks] == [
        "First page evidence.",
        "Second page evidence.",
    ]
    with pytest.raises(DomainError, match="Tesseract") as missing:
        parse("scanned-bilingual.pdf")
    assert missing.value.code == "PROVIDER_UNAVAILABLE"
    assert parse("scanned-bilingual.pdf", ParseOptions(ocr=False)).warnings == ("OCR_REQUIRED",)


def test_pdf_respects_the_configured_page_limit():
    with pytest.raises(DomainError, match="configured page limit") as failure:
        parse("native.pdf", ParseOptions(max_pages=1))
    assert failure.value.code == "QUOTA_EXCEEDED"


def test_failed_ocr_does_not_publish_blank_or_leak_engine_diagnostics(monkeypatch):
    native_run = subprocess.run

    def failure(command, **kwargs):
        if command[0] == sys.executable:
            return native_run(command, **kwargs)
        return subprocess.CompletedProcess(command, 1, stderr=b"private original contents")

    monkeypatch.setattr(subprocess, "run", failure)
    with pytest.raises(DomainError, match="Local OCR failed") as failed:
        parse("scanned-bilingual.pdf")
    assert "private" not in failed.value.message


def test_ocr_provenance_is_only_valid_for_pdf_locations():
    with pytest.raises(ValueError):
        SourceLocation(kind="text", block_id=uuid4(), text_origin="ocr")


def test_legacy_native_source_locations_keep_their_sealed_payload():
    legacy = {
        "kind": "pdf",
        "block_id": str(uuid4()),
        "page": 1,
        "paragraph": None,
        "table": None,
        "row": None,
        "json_pointer": None,
        "heading": None,
    }
    restored = SourceLocation.model_validate(legacy)
    assert restored.text_origin == "native"
    assert restored.model_dump(mode="json") == legacy


@pytest.mark.parametrize("fault", ["timeout", "output"])
def test_ocr_timeout_and_excessive_output_are_resource_failures(monkeypatch, fault):
    native_run = subprocess.run

    def engine(command, **kwargs):
        if command[0] == sys.executable:
            return native_run(command, **kwargs)
        if fault == "timeout":
            raise subprocess.TimeoutExpired(command, 30)
        kwargs["stdout"].write(b"x" * (1024 * 1024 + 1))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", engine)
    with pytest.raises(DomainError) as exceeded:
        parse("scanned-bilingual.pdf")
    assert exceeded.value.code == "QUOTA_EXCEEDED"


def test_console_explains_ocr_result_and_local_engine_failure_in_chinese():
    from domain_kg.console.i18n import LANGUAGE, label, translate
    from domain_kg.jobs.diagnostics import failure_copy

    token = LANGUAGE.set("zh-CN")
    try:
        warning = label("OCR_APPLIED")
        assert "识别" in warning and "核对" in warning
        summary, action = failure_copy("PROVIDER_UNAVAILABLE", "Local PDF OCR failed.")
        assert summary == "Local PDF OCR failed."
        assert "Tesseract" in action and "language" in action
        assert "识别" in translate(summary)
        assert "语言" in translate(action)
    finally:
        LANGUAGE.reset(token)


async def test_parser_memory_monitor_includes_the_ocr_child():
    import asyncio
    import os
    import signal
    import sys

    from domain_kg.ingestion.parsers import Parser

    code = """import subprocess, sys
p = subprocess.Popen([sys.executable, '-c',
    'import time; data = bytearray(24 * 1024 * 1024); print("ready", flush=True); time.sleep(15)'])
p.wait()
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        start_new_session=True,
        stdout=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(5):
            assert await process.stdout.readline() == b"ready\n"
        assert await Parser._rss(process) > 30 * 1024 * 1024
    finally:
        os.killpg(process.pid, signal.SIGKILL)
        await process.wait()


@pytest.mark.parametrize(
    ("media_type", "rss_mib", "accepted"),
    [("application/pdf", 600, True), ("application/pdf", 1025, False), ("text/plain", 600, True)],
)
async def test_pdf_memory_budget_accepts_larger_jobs_but_keeps_a_hard_stop(
    monkeypatch, media_type, rss_mib, accepted
):
    from domain_kg.ingestion.parsers import Parser

    data = (
        (FIXTURES / "native.pdf").read_bytes() if media_type == "application/pdf" else b"Evidence."
    )
    blob = BlobRef(
        org_id=uuid4(),
        kg_id=uuid4(),
        material_id=uuid4(),
        version_id=uuid4(),
        object_id=uuid4(),
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        media_type=media_type,
    )

    class Original:
        async def open(self, reference):
            assert reference == blob
            yield data

    async def measured_rss(_process):
        # Exercise admission at the memory-monitor boundary without allocating a GiB.
        return rss_mib * 1024 * 1024

    parser = Parser(Original())
    monkeypatch.setattr(parser, "_rss", measured_rss)
    if accepted:
        document = await parser.parse(blob, ParseOptions())
        expected = (
            ["First page evidence.", "Second page evidence."]
            if media_type == "application/pdf"
            else ["Evidence."]
        )
        assert [block.text for block in document.blocks] == expected
    else:
        with pytest.raises(DomainError, match="Parser memory limit exceeded") as failure:
            await parser.parse(blob, ParseOptions())
        assert failure.value.code == "QUOTA_EXCEEDED"


async def test_parser_cleans_ocr_child_when_the_parent_exits(tmp_path, monkeypatch):
    import asyncio
    import os
    import signal
    import sys

    from domain_kg.ingestion.parsers import Parser

    data = b"Synthetic source"
    blob = BlobRef(
        org_id=uuid4(),
        kg_id=uuid4(),
        material_id=uuid4(),
        version_id=uuid4(),
        object_id=uuid4(),
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        media_type="text/plain",
    )

    class Original:
        async def open(self, _reference):
            yield data

    pidfile = tmp_path / "ocr-pid"
    code = f"""import subprocess, sys
from pathlib import Path
p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
Path({str(pidfile)!r}).write_text(str(p.pid))
"""
    parser = Parser(Original())
    monkeypatch.setattr(parser, "_command", lambda *_: [sys.executable, "-c", code])
    with pytest.raises(DomainError):
        await parser.parse(blob, ParseOptions(timeout_seconds=5))
    child = int(pidfile.read_text())
    try:
        async with asyncio.timeout(3):
            while True:
                check = await asyncio.create_subprocess_exec(
                    "/bin/ps",
                    "-o",
                    "stat=",
                    "-p",
                    str(child),
                    stdout=asyncio.subprocess.PIPE,
                )
                state, _ = await check.communicate()
                if not state.strip() or state.strip().startswith(b"Z"):
                    break
                await asyncio.sleep(0.05)
    finally:
        try:
            os.kill(child, signal.SIGKILL)
        except ProcessLookupError:
            pass


async def test_small_multi_page_scans_complete_inside_the_existing_memory_limit():
    """Retaining completed renderer buffers must not crowd out the next OCR child."""
    import io

    from PIL import Image, ImageDraw

    from domain_kg.ingestion.parsers import Parser

    pages = []
    try:
        for number in range(6):
            image = Image.new("RGB", (1819, 2572), "white")
            draw = ImageDraw.Draw(image)
            for top in range(80, 2500, 180):
                draw.text(
                    (50, top),
                    f"Scan page {number + 1} evidence for offline memory regression {top}",
                    fill="black",
                    font_size=36,
                )
            pages.append(image)
        buffer = io.BytesIO()
        pages[0].save(buffer, "PDF", save_all=True, append_images=pages[1:])
        data = buffer.getvalue()
    finally:
        for image in pages:
            image.close()
    assert len(data) < 2 * 1024 * 1024
    blob = BlobRef(
        org_id=uuid4(),
        kg_id=uuid4(),
        material_id=uuid4(),
        version_id=uuid4(),
        object_id=uuid4(),
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        media_type="application/pdf",
    )

    class Original:
        async def open(self, reference):
            assert reference == blob
            yield data

    document = await Parser(Original()).parse(blob, ParseOptions(timeout_seconds=120))
    assert len(document.images) == 6
    assert {block.location.page for block in document.blocks} == set(range(1, 7))
    for number in range(1, 7):
        assert any(
            f"Scan page {number}" in block.text and block.location.page == number
            for block in document.blocks
        )


def test_native_pdf_does_not_materialize_docling_geometry(monkeypatch):
    # Native text must remain readable without importing the heavy geometry
    # decoder. Docling is loaded only by a short-lived rendering child for OCR.
    monkeypatch.setitem(sys.modules, "docling_parse.pdf_parser", None)
    document = parse("native.pdf")
    assert [(block.text, block.location.page) for block in document.blocks] == [
        ("First page evidence.", 1),
        ("Second page evidence.", 2),
    ]


def test_broken_embedded_image_preserves_readable_native_pdf_evidence():
    import io

    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DictionaryObject, IndirectObject, NameObject

    reader = PdfReader(FIXTURES / "native.pdf")
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.pages[0][NameObject("/Resources")][NameObject("/XObject")] = DictionaryObject(
        {NameObject("/Missing"): IndirectObject(9999, 0, writer)}
    )
    buffer = io.BytesIO()
    writer.write(buffer)
    data = buffer.getvalue()
    blob = BlobRef(
        org_id=uuid4(),
        kg_id=uuid4(),
        material_id=uuid4(),
        version_id=uuid4(),
        object_id=uuid4(),
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        media_type="application/pdf",
    )
    document = decode(data, blob, ParseOptions())
    assert [block.text for block in document.blocks] == [
        "First page evidence.",
        "Second page evidence.",
    ]
    assert "PDF_IMAGE_UNREADABLE" in document.warnings
