"""Real preserved originals through a bounded, network-disabled native parser."""

import hashlib
import io
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from pypdf import PdfReader, PdfWriter, Transformation

from domain_kg.errors import DomainError
from domain_kg.ingestion.models import ParseOptions
from domain_kg.materials.models import BlobScope

pytestmark = pytest.mark.storage
FIXTURES = Path(__file__).parents[1] / "fixtures" / "materials"


async def original(blob_store, data, media_type):
    blobs, kg, org = blob_store

    async def stream():
        yield data

    return await blobs.put(
        stream(),
        hashlib.sha256(data).hexdigest(),
        BlobScope(org_id=org, kg_id=kg, material_id=uuid4(), version_id=uuid4()),
        media_type=media_type,
    )


@pytest.mark.parametrize(
    ("media_type", "data", "expected"),
    [
        ("text/plain", "  Unicode 细胞 evidence.\n", "  Unicode 细胞 evidence.\n"),
        ("text/markdown", "# Heading\n\nEvidence in a paragraph.\n", "Evidence in a paragraph."),
        (
            "text/html",
            '<h1>Heading</h1><script>leak()</script><p>Visible evidence</p><img src="http://127.0.0.1:27027/">',
            "Visible evidence",
        ),
        ("text/csv", 'name,evidence\ncell,"Observed, precise"\n', "Observed, precise"),
        ("application/json", '{"items":[{"a/b":"细胞"}]}', "细胞"),
    ],
)
async def test_deterministic_parsers_preserve_structured_locations(
    blob_store, media_type, data, expected
):
    from domain_kg.ingestion.parsers import Parser

    blob = await original(blob_store, data.encode(), media_type)
    parsed = await Parser(blob_store[0]).parse(blob, ParseOptions())
    assert expected in "".join(block.text for block in parsed.blocks)
    assert all(block.org_id == blob.org_id and block.kg_id == blob.kg_id for block in parsed.blocks)
    if media_type == "text/html":
        assert "leak()" not in "".join(block.text for block in parsed.blocks)
    if media_type == "application/json":
        assert parsed.blocks[0].location.json_pointer == "/items/0/a~1b"
    if media_type == "text/csv":
        assert parsed.blocks[-1].location.row == 2


async def test_native_docling_pdf_and_docx_citations_match_originals(blob_store):
    from domain_kg.ingestion.parsers import Parser

    parser = Parser(blob_store[0])
    pdf = await original(blob_store, (FIXTURES / "native.pdf").read_bytes(), "application/pdf")
    parsed = await parser.parse(pdf, ParseOptions())
    assert [block.text for block in parsed.blocks] == [
        "First page evidence.",
        "Second page evidence.",
    ]
    assert [block.location.page for block in parsed.blocks] == [1, 2]
    assert "docling-parse" in parsed.parser_version
    docx_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    docx = await original(blob_store, (FIXTURES / "headings-table.docx").read_bytes(), docx_type)
    parsed = await parser.parse(docx, ParseOptions())
    paragraph = next(block for block in parsed.blocks if "细胞" in block.text)
    assert paragraph.text == "  细胞 evidence paragraph." and paragraph.location.paragraph == 1
    assert paragraph.location.heading == "Research heading"
    row = next(block for block in parsed.blocks if "Observed" in block.text)
    assert row.location.table == 0 and row.location.row == 1


async def test_scan_warns_without_ocr_and_malformed_or_encrypted_pdf_is_rejected(blob_store):
    from domain_kg.ingestion.parsers import Parser

    parser = Parser(blob_store[0])
    blob = await original(
        blob_store, (FIXTURES / "scanned-bilingual.pdf").read_bytes(), "application/pdf"
    )
    parsed = await parser.parse(blob, ParseOptions(ocr=False))
    assert not parsed.blocks and "OCR_REQUIRED" in parsed.warnings
    for data in [(FIXTURES / "encrypted.pdf").read_bytes(), b"%PDF-not-a-document"]:
        with pytest.raises(DomainError):
            await parser.parse(await original(blob_store, data, "application/pdf"), ParseOptions())


async def test_scanned_pdf_automatically_recognizes_bilingual_evidence_with_page_provenance(
    blob_store,
):
    from domain_kg.ingestion.chunking import chunk_document
    from domain_kg.ingestion.models import ChunkConfig
    from domain_kg.ingestion.parsers import Parser

    blob = await original(
        blob_store, (FIXTURES / "scanned-bilingual.pdf").read_bytes(), "application/pdf"
    )
    parsed = await Parser(blob_store[0]).parse(blob, ParseOptions())
    text = "".join(block.text for block in parsed.blocks)
    assert "Scanned evidence" in text
    assert "扫描证据" in "".join(text.split())
    assert {block.location.page for block in parsed.blocks} == {1}
    assert all(block.location.text_origin == "ocr" for block in parsed.blocks)
    assert "OCR_APPLIED" in parsed.warnings and "OCR_REQUIRED" not in parsed.warnings
    chunks = chunk_document(parsed, ChunkConfig())
    for chunk in chunks:
        for span in chunk.spans:
            block = next(block for block in parsed.blocks if block.id == span.location.block_id)
            assert block.text[span.start : span.end] == span.quote
            assert span.location.text_origin == "ocr"


async def test_mixed_pdf_keeps_native_text_once_and_recognizes_scan_at_physical_page(blob_store):
    from domain_kg.ingestion.parsers import Parser

    native = PdfReader(FIXTURES / "native.pdf")
    scan = PdfReader(FIXTURES / "scanned-bilingual.pdf")
    writer, data = PdfWriter(), io.BytesIO()
    writer.add_page(native.pages[0])
    page = writer.add_page(scan.pages[0])
    page.mediabox.upper_right = (400, 170)
    header = PdfWriter().add_page(native.pages[1])
    header.add_transformation(Transformation().translate(tx=-60, ty=-570))
    page.merge_page(header)
    writer.write(data)
    blob = await original(blob_store, data.getvalue(), "application/pdf")
    parsed = await Parser(blob_store[0]).parse(blob, ParseOptions())
    assert [block.text for block in parsed.blocks if block.location.text_origin == "native"] == [
        "First page evidence.",
        "Second page evidence.",
    ]
    ocr = [block for block in parsed.blocks if block.location.text_origin == "ocr"]
    assert ocr and {block.location.page for block in ocr} == {2}
    assert "Scanned evidence" in "".join(block.text for block in ocr)
    assert all("Second page" not in block.text for block in ocr)


async def test_page_limit_and_zip_expansion_or_traversal_are_rejected_before_work(blob_store):
    from domain_kg.ingestion.parsers import Parser

    parser = Parser(blob_store[0])
    writer, buffer = PdfWriter(), io.BytesIO()
    for _ in range(501):
        writer.add_blank_page(width=10, height=10)
    writer.write(buffer)
    blob = await original(blob_store, buffer.getvalue(), "application/pdf")
    with pytest.raises(DomainError) as over_limit:
        await parser.parse(blob, ParseOptions())
    assert over_limit.value.code == "QUOTA_EXCEEDED"
    media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    for name, data in [("word/document.xml", "x" * 4096), ("../outside", "x")]:
        buffer = io.BytesIO()
        with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
            archive.writestr(name, data)
        blob = await original(blob_store, buffer.getvalue(), media_type)
        with pytest.raises(DomainError):
            await parser.parse(blob, ParseOptions(max_expanded_bytes=1024))


async def test_preparation_pipeline_preserves_ledger_without_claiming_publication(
    material_service, team, mongo_db
):
    from domain_kg.identity.models import Action
    from domain_kg.ingestion.pipeline import Pipeline, preparation_steps
    from domain_kg.jobs.queue import JobQueue
    from domain_kg.jobs.runner import JobRunner
    from domain_kg.materials.models import AddMaterial

    service, kg = material_service
    _, policy, principals, _, org, _ = team
    access = await policy.resolve_access(principals["editor"], [kg], Action.INGEST)
    receipt = await service.add(
        access,
        AddMaterial(
            kg_id=kg,
            title="Source ledger",
            inline_text="Evidence one.\nEvidence two.",
            idempotency_key="ledger",
        ),
    )
    queue = JobQueue(policy, service.router)
    await queue.initialize()
    runner = JobRunner(queue, Pipeline(preparation_steps(queue)))
    await runner.run_job(await queue.claim("worker"))
    ns = {"org_id": str(org), "kg_id": str(kg)}
    assert (
        await mongo_db.source_blocks.count_documents(ns | {"version_id": str(receipt.version_id)})
        == 2
    )
    assert (
        await mongo_db.source_chunks.count_documents(ns | {"version_id": str(receipt.version_id)})
        == 1
    )
    status = await queue.get(access, receipt.job_id)
    assert status.stage == "repair_required"
    material = await mongo_db.material_access.find_one(ns | {"id": str(receipt.material_id)})
    assert material["active_version_id"] is None
    await mongo_db.jobs.update_one(
        ns | {"id": str(receipt.job_id)}, {"$set": {"available_at": datetime.now(UTC)}}
    )
    await runner.run_job(await queue.claim("recovered"))
    assert await mongo_db.source_blocks.count_documents(ns) == 2
    assert await mongo_db.source_chunks.count_documents(ns) == 1


async def test_xml_entities_and_duplicate_json_keys_cannot_create_evidence(blob_store):
    from domain_kg.ingestion.parsers import Parser

    parser = Parser(blob_store[0])
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "word/document.xml",
            """<!DOCTYPE document [
<!ENTITY secret SYSTEM "file:///etc/passwd">
]>
<w:document
xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body><w:p>
<w:r><w:t>&secret;</w:t></w:r>
</w:p></w:body></w:document>""",
        )
    media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    with pytest.raises(DomainError):
        await parser.parse(
            await original(blob_store, buffer.getvalue(), media_type), ParseOptions()
        )
    with pytest.raises(DomainError):
        await parser.parse(
            await original(blob_store, b'{"claim":"one","claim":"two"}', "application/json"),
            ParseOptions(),
        )


async def test_parser_runtime_and_memory_limits_stop_work_without_heavy_inputs(
    blob_store, monkeypatch
):
    import sys
    import time

    from domain_kg.ingestion.parsers import MAX_RSS_BYTES, Parser

    parser = Parser(blob_store[0])
    blob = await original(blob_store, b"Tiny evidence", "text/plain")
    monkeypatch.setattr(
        parser, "_command", lambda *_: [sys.executable, "-c", "import time; time.sleep(5)"]
    )
    start = time.monotonic()
    with pytest.raises(DomainError) as timeout:
        await parser.parse(blob, ParseOptions(timeout_seconds=1))
    assert timeout.value.code == "QUOTA_EXCEEDED" and time.monotonic() - start < 3

    async def over_limit(_process):
        return MAX_RSS_BYTES + 1

    monkeypatch.setattr(parser, "_rss", over_limit)
    with pytest.raises(DomainError) as memory:
        await parser.parse(blob, ParseOptions())
    assert memory.value.code == "QUOTA_EXCEEDED"


async def test_native_macos_profile_denies_network(tmp_path):
    import asyncio
    import sys

    from domain_kg.ingestion.parsers import Parser

    if sys.platform != "darwin":
        pytest.skip("macOS kernel sandbox check; Linux worker has separate process limits")
    code = """import socket
try:
    socket.socket().connect(("127.0.0.1", 9))
except PermissionError:
    print("blocked")
"""
    process = await asyncio.create_subprocess_exec(
        *Parser._command(tmp_path, [sys.executable, "-c", code]),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        cwd=tmp_path,
    )
    output, _ = await process.communicate()
    assert process.returncode == 0 and output.strip() == b"blocked"
