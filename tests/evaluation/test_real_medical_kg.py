"""Opt-in genuine model calls, entire PDF, actual stores and source-scoped UI.

Only the local preview identity uses the existing test issuer. No model, embedding
or storage response is simulated. This is not external OIDC acceptance.
"""
# ruff: noqa: F811

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

import httpx
import pytest
from jinja2 import ChoiceLoader, DictLoader
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_medical_pdf import serve

from domain_kg.app import create_app
from domain_kg.console import rendering
from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.ingestion.models import ChunkConfig, ParseOptions
from domain_kg.ingestion.pipeline import extraction_steps, preparation_steps
from domain_kg.ingestion.repository import SourceRepository
from domain_kg.jobs.queue import JobQueue
from domain_kg.materials.models import AddMaterial, UploadCommand
from domain_kg.models import Namespace
from domain_kg.providers.profiles import ProviderConfig, ProviderRegistry
from domain_kg.providers.service import ProviderService
from domain_kg.retrieval.models import SearchFilters
from domain_kg.revisions.models import PublishRevision
from domain_kg.revisions.publication import ProjectionCoordinator
from domain_kg.settings import Settings
from scripts.medical_extract import (
    api_registry,
    configuration,
    connections,
    profile_for,
    provider_fingerprint,
)
from scripts.native_model import CACHE, ROOT, model

pytestmark = [
    pytest.mark.storage,
    pytest.mark.skipif(
        not os.environ.get("DOMAIN_KG_REAL_MEDICAL_PDF"), reason="real native corpus run is opt-in"
    ),
]


@pytest.fixture
def catalog_dimensions():
    return 1024


@pytest.fixture
def material_domain_profile():
    return "medical"


class RealProviders(ProviderService):
    """Telemetry/limits around the real client; never supplies model output."""

    events = None
    attempted = 0

    async def extract(self, access, chunk, schema, **kwargs):
        settings = configuration()
        if self.events is None:
            self.events = []
        usage = await self.database.provider_usage.find_one(
            {"org_id": str(chunk.org_id), "kg_id": str(chunk.kg_id)}
        )
        if self.attempted >= settings["full_document_max_requests"] or (
            usage and usage["output_tokens"] >= settings["full_document_max_output_tokens"]
        ):
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Explicit real PDF model budget reached.")
        self.attempted += 1
        event = {
            "chunk_id": str(chunk.id),
            "pages": sorted({s.location.page for s in chunk.spans}),
            "source_characters": len(chunk.text),
        }
        started = time.monotonic()
        try:
            result = await super().extract(access, chunk, schema, **kwargs)
            event.update(
                status="validated", entities=len(result.entities), assertions=len(result.assertions)
            )
            return result
        except DomainError as error:
            event.update(status="rejected", code=str(error.code), reason=type(error).__name__)
            raise
        finally:
            event["seconds"] = round(time.monotonic() - started, 3)
            self.events.append(event)
            (CACHE / "real-medical-kg/run-progress.json").write_text(
                json.dumps(self.events, indent=2)
            )
            print("Real extraction: " + json.dumps(event), flush=True)


async def test_entire_real_medical_pdf(
    material_service, team, catalog, issuer_fixture, monkeypatch
):
    settings = configuration()
    quality = json.loads((CACHE / "real-medical-kg/quality-report.json").read_bytes())
    assert quality["passed"]
    assert quality["protocol"] == settings["protocol"]
    assert quality["domain_schema"] == settings["domain_schema"]
    run_config = json.loads((ROOT / "config/medical-pdf-live.json").read_bytes())
    source_config = json.loads((ROOT / run_config["source_manifest"]).read_bytes())
    pdf = Path(os.environ["DOMAIN_KG_REAL_MEDICAL_PDF"])
    original = await asyncio.to_thread(pdf.read_bytes)
    assert hashlib.sha256(original).hexdigest() == source_config["sha256"] == quality["pdf_sha256"]
    materials, kg = material_service
    identities, policy, principals, _, org, _ = team
    owner = await policy.resolve_access(principals["owner"], [kg], Action.CONFIGURE)
    profile = profile_for(Namespace(org_id=org, kg_id=kg))
    assert quality["provider_fingerprint"] == provider_fingerprint(profile)
    providers = RealProviders(
        policy,
        materials.router,
        ProviderRegistry(ProviderConfig(profiles=(profile,), connections=connections())),
    )
    await providers.bind(
        owner, kg, profile.id, expected_revision=0, idempotency_key="real-medical-provider"
    )
    editor = await policy.resolve_access(principals["editor"], [kg], Action.INGEST)
    queue = JobQueue(policy, materials.router, lease_seconds=120)
    await queue.initialize()
    coordinator = ProjectionCoordinator(queue, providers)

    async def stream():
        for i in range(0, len(original), 65536):
            yield original[i : i + 65536]

    upload = await materials.upload(
        editor,
        UploadCommand(
            kg_id=kg,
            media_type="application/pdf",
            sha256=source_config["sha256"],
            size_bytes=len(original),
            idempotency_key="real-medical-upload",
        ),
        stream(),
    )
    source = await materials.add(
        editor,
        AddMaterial(
            kg_id=kg,
            upload_id=upload.upload_id,
            title=source_config["title"] + " [" + profile.extraction_model + " real extraction]",
            idempotency_key="real-medical-material",
        ),
    )
    job = await queue.claim("real-medical-native")
    preparation = preparation_steps(
        queue,
        providers=providers,
        options=ParseOptions(**source_config["parse"]),
        chunk_config=ChunkConfig(**run_config["chunks"]),
    )
    extracting = extraction_steps(queue, providers, preparation[-1])
    document = None
    metrics = {
        "pdf_sha256": source_config["sha256"],
        "model": profile.extraction_model,
        "model_sha256": quality["model_sha256"],
        "provider_fingerprint": quality["provider_fingerprint"],
        "protocol": profile.extraction_format,
        "kg_id": str(kg),
        "material_id": str(source.material_id),
        "version_id": str(source.version_id),
        "provider_mode": "real_api" if api_registry() else "real_native",
        "fixture_responses": 0,
    }

    async def work():
        nonlocal document
        for step in preparation:
            checkpoint = await step.execute(job)
            await queue.checkpoint(job, checkpoint)
        document = await SourceRepository(queue).read(job, checkpoint)
        assert len(document.chunks) <= run_config["max_chunks"]
        for block in document.blocks:
            covered = {
                i
                for c in document.chunks
                for s in c.spans
                if s.location == block.location
                for i in range(s.start, s.end)
            }
            assert covered == set(range(len(block.text)))
        if api_registry():
            checkpoint = await extracting[0].execute(job)
            await queue.checkpoint(job, checkpoint)
            metrics["chat_peak_rss_bytes"] = 0
        else:
            async with model("chat") as chat:
                checkpoint = await extracting[0].execute(job)
                await queue.checkpoint(job, checkpoint)
            metrics["chat_peak_rss_bytes"] = chat.peak_rss_bytes
        checkpoint = await extracting[1].execute(job)
        await queue.checkpoint(job, checkpoint)
        async with model("embedding") as embedding:
            embedded = await coordinator.embed(job, checkpoint)
            await queue.checkpoint(job, embedded)
        metrics["embedding_peak_rss_bytes"] = embedding.peak_rss_bytes
        batch = await coordinator.prepare(job, embedded)
        staged = await coordinator.stage(editor, batch)
        ready = await coordinator.verify(editor, staged)
        assert ready.ready
        lease = await queue.acquire_publication(job)
        await coordinator.publish(
            editor,
            PublishRevision(
                report=ready,
                lease_token=lease.lease_token,
                fence=lease.fence,
                expected_revision=batch.expected_revision,
                idempotency_key="real-medical-publish",
            ),
        )
        assert await queue.accepted(job)
        await queue.finish(job)

    async def renew():
        while True:
            await asyncio.sleep(10)
            await queue.heartbeat(job)
            await queue.authorize(job)

    task, heartbeat = asyncio.create_task(work()), asyncio.create_task(renew())
    try:
        async with asyncio.timeout(run_config["whole_run_timeout_seconds"]):
            done, _ = await asyncio.wait((task, heartbeat), return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                await heartbeat
            await task
    finally:
        task.cancel()
        heartbeat.cancel()
        await asyncio.gather(task, heartbeat, return_exceptions=True)
    assert document is not None
    graph = GraphService(queue)
    reader = await policy.resolve_access(principals["reader"], [kg], Action.READ)
    filters = SearchFilters(material_ids=(source.material_id,), version_id=source.version_id)
    view, total = await graph.overview(reader, kg, filters)
    assert total > 2 and len(view.edges) > 1
    usage = await queue.database.provider_usage.find_one({"org_id": str(org), "kg_id": str(kg)})
    metrics.update(
        pages=len({b.location.page for b in document.blocks}),
        blocks=len(document.blocks),
        chunks=len(document.chunks),
        processed_chunks=len(document.chunks),
        input_coverage_complete=True,
        visible_nodes=total,
        displayed_nodes=len(view.nodes),
        displayed_edges=len(view.edges),
        extraction_attempts=providers.attempted,
        extraction_events=providers.events,
        provider_usage={k: usage[k] for k in ("input_tokens", "output_tokens", "calls")},
    )
    (CACHE / "real-medical-kg/run-report.json").write_text(json.dumps(metrics, indent=2))
    templates = rendering.TEMPLATES
    base, _, _ = templates.loader.get_source(templates, "base.html")
    banner = (
        '<section class="card" id="real-model-run"><p lang="zh-CN">'
        "<strong>真实模型：全部 PDF 文本窗口已处理</strong>。"
        "全部文本块已处理；不等同于医学准确性或事实召回率已获独立验证。"
        '本机预览登录使用测试会话。</p><p lang="en">'
        "<strong>Real model; all PDF source windows processed.</strong> "
        "No simulated model/embedding responses. "
        "Input coverage does not establish clinical accuracy or complete semantic recall. "
        "Local preview authentication uses a test session.</p></section>"
    )
    environment = templates.overlay(
        loader=ChoiceLoader(
            [
                DictLoader(
                    {
                        "base.html": base.replace(
                            '<main id="content" tabindex="-1">',
                            '<main id="content" tabindex="-1">'
                            + "{% if graph_data is defined and graph_data.nodes %}"
                            + banner
                            + "{% endif %}",
                        )
                    }
                ),
                templates.loader,
            ]
        )
    )
    monkeypatch.setattr(rendering, "TEMPLATES", environment)
    _, state, transport, _ = issuer_fixture
    state["issuer"] = principals["owner"].issuer
    async with httpx.AsyncClient(transport=transport) as oidc:
        app = create_app(
            Settings(
                _env_file=ROOT / ".env",
                environment="test",
                public_url="http://localhost:28080",
                oidc_issuer=state["issuer"],
                oidc_client_id="console",
                retention_config=ROOT / "config/retention.example.json",
            ),
            identity_repository=identities,
            oidc_http_client=oidc,
            catalog_service=catalog[0],
            providers=providers,
        )
        target = (
            f"/admin/kgs/{kg}/graph?material_id={source.material_id}&version_id={source.version_id}"
        )
        async with app.router.lifespan_context(app):
            for role in ("owner", "reader", "denied"):
                ticket = await app.state.identity.sessions.create(principals[role], org)
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://localhost:28080",
                    cookies={"domain_kg_session": ticket.session_id},
                ) as browser:
                    page = await browser.get(target)
                    if role == "denied":
                        assert page.status_code == 404 and "data-graph=" not in page.text
                        assert "real-model-run" not in page.text
                    else:
                        page.raise_for_status()
                        assert "real-model-run" in page.text and "data-graph=" in page.text
            print("Real PDF KG published: " + json.dumps(metrics), flush=True)
            if os.environ.get("DOMAIN_KG_BROWSER_PREVIEW") == "1":
                await serve(app, target, metrics, principals, org, run_config["preview_seconds"])
