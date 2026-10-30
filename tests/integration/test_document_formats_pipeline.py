"""Readable file formats reach one verified KG through the compact provider protocol."""

import hashlib
import json
from pathlib import Path

import httpx
import pytest
from test_entity_resolution import TEXT
from test_publication import make_publishing

from domain_kg.ingestion.pipeline import Pipeline, publication_steps
from domain_kg.jobs.runner import JobRunner
from domain_kg.materials.models import AddMaterial, UploadCommand
from domain_kg.revisions.journal import PublicationJournal
from domain_kg.revisions.publication import accepted_document

pytestmark = pytest.mark.storage
FIXTURES = Path(__file__).parents[1] / "fixtures/materials"


async def test_supported_text_documents_publish_verified_graphs_in_the_same_kg(
    material_service, team
):
    publishing = await make_publishing(
        material_service,
        team,
        profile_overrides={
            "extraction_format": "source_names_v1",
            "extraction_response_format": "json_object",
            "max_completion_tokens": 2048,
        },
    )
    materials, access, queue, coordinator, steps, _ = publishing
    provider_calls = []

    def response(request):
        body = json.loads(request.content)
        provider_calls.append(request.url.path)
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": index, "embedding": [1.0, 0.1, 0.2]}
                        for index, _ in enumerate(body["input"])
                    ],
                    "model": "fixture-embedding",
                    "usage": {"prompt_tokens": 10},
                },
            )
        sources = json.loads(body["messages"][1]["content"])["sources"]
        refs = [s["id"] for s in sources if "Alpha" in s["text"] and "Beta" in s["text"]]
        if refs:
            content = {
                "entities": [
                    {"name": name, "type": "concept", "refs": refs} for name in ("Alpha", "Beta")
                ],
                "assertions": [
                    {
                        "subject": "Alpha",
                        "target": "Beta",
                        "predicate": "uses",
                        "polarity": "positive",
                        "confidence": 0.9,
                        "refs": refs,
                        "qualifiers": [],
                    }
                ],
            }
        else:
            refs = [s["id"] for s in sources if "evidence" in s["text"]]
            content = {
                "entities": [{"name": "evidence", "type": "concept", "refs": refs}] if refs else [],
                "assertions": [],
            }
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    coordinator.providers.transport = httpx.MockTransport(response)
    runner = JobRunner(queue, Pipeline(steps + publication_steps(queue, coordinator, steps[-1])))
    journal = PublicationJournal(queue)
    documents = [
        ("pdf", "application/pdf", (FIXTURES / "native.pdf").read_bytes()),
        ("scan", "application/pdf", (FIXTURES / "scanned-bilingual.pdf").read_bytes()),
        (
            "docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            (FIXTURES / "headings-table.docx").read_bytes(),
        ),
        ("html", "text/html", f"<h1>Source</h1><p>{TEXT}</p><script>private()</script>".encode()),
        ("txt", "text/plain", TEXT.encode()),
        ("md", "text/markdown", f"# Source\n\n{TEXT}".encode()),
        ("csv", "text/csv", ('claim\n"' + TEXT + '"\n').encode()),
        ("json", "application/json", json.dumps({"claim": TEXT}, ensure_ascii=False).encode()),
    ]
    from domain_kg.ingestion.file_types import LEGACY, ODP, PPTX, XLSX

    documents.extend(
        (name, mime, (FIXTURES / name).read_bytes())
        for name, mime in [
            ("workbook.xlsx", XLSX),
            ("slides.pptx", PPTX),
            ("slides.odp", ODP),
            *(
                (
                    {
                        "doc": "headings-table.doc",
                        "rtf": "headings-table.rtf",
                        "odt": "headings-table.odt",
                        "xls": "workbook.xls",
                        "ods": "workbook.ods",
                        "ppt": "slides.ppt",
                        "odp": "slides.odp",
                    }[ext],
                    mime,
                )
                for mime, (ext, _, _) in LEGACY.items()
                if mime != "text/rtf"
            ),
            ("image.png", "image/png"),
        ]
        if name != "image.png"
    )
    receipts = []
    for extension, media_type, data in documents:

        async def stream(original=data):
            yield original

        upload = await materials.upload(
            access,
            UploadCommand(
                kg_id=access.kg_ids[0],
                media_type=media_type,
                sha256=hashlib.sha256(data).hexdigest(),
                size_bytes=len(data),
                idempotency_key="upload-" + extension,
            ),
            stream(),
        )
        receipt = await materials.add(
            access,
            AddMaterial(
                kg_id=access.kg_ids[0],
                title="fixture." + extension,
                upload_id=upload.upload_id,
                idempotency_key="add-" + extension,
            ),
        )
        await runner.run_job(await queue.claim("formats"))
        status = await queue.get(access, receipt.job_id)
        assert status.stage in ("ready", "ready_with_warnings"), (
            extension,
            status.error,
        )
        view = await journal.load(access, access.kg_ids[0])
        binding = next(p for p in view.projections if p.source.version_id == receipt.version_id)
        projection, _, acknowledgements = await accepted_document(queue, access, binding)
        assert {r.backend for r in acknowledgements} == {"graph", "vector", "lexical"}
        assert any(row.kind == "observation" for row in projection.rows)
        chunks = [row.chunk for row in projection.rows if row.chunk is not None]
        assert chunks and all(chunk.spans for chunk in chunks)
        if extension == "pdf":
            assert {span.location.page for chunk in chunks for span in chunk.spans} == {1, 2}
        if extension == "scan":
            assert "OCR_APPLIED" in status.warnings
            assert {span.location.page for chunk in chunks for span in chunk.spans} == {1}
            assert all(
                span.location.text_origin == "ocr" for chunk in chunks for span in chunk.spans
            )
        if extension == "docx":
            assert any(span.location.table is not None for chunk in chunks for span in chunk.spans)
        if extension == "html":
            assert all("private()" not in chunk.text for chunk in chunks)
        if extension in (
            "html",
            "txt",
            "md",
            "csv",
            "json",
            "workbook.xlsx",
            "workbook.xls",
            "workbook.ods",
            "slides.pptx",
            "slides.ppt",
            "slides.odp",
        ):
            assert any(row.kind == "support" for row in projection.rows)
        receipts.append(receipt)
    view = await journal.load(access, access.kg_ids[0])
    assert view.revision == len(documents)
    assert {s.version_id for s in view.sources} == {r.version_id for r in receipts}
    assert len(view.projections) == len(documents)
    assert provider_calls.count("/v1/embeddings") >= len(documents)
