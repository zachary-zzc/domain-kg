"""Local PDF OCR never hides missing prerequisites or accepts failed OCR output."""

import hashlib
import shutil
import subprocess
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


def test_failed_ocr_does_not_publish_blank_or_leak_engine_diagnostics(monkeypatch):
    def failure(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1, stderr=b"private original contents")

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
    def engine(command, **kwargs):
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
