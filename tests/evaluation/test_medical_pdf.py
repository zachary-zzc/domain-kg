"""Opt-in real medical PDF, native bounded parser, genuine stores and controlled responses."""
# ruff: noqa: F811

import asyncio
import hashlib
import json
import os
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal

import httpx
import pytest
import uvicorn
from fastapi.responses import RedirectResponse
from medical_fixture import medical_batch
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_publication import make_publishing, ready

from domain_kg.app import create_app
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.ingestion.models import ChunkConfig, ParseOptions
from domain_kg.ingestion.pipeline import extraction_steps, preparation_steps
from domain_kg.ingestion.repository import SourceRepository
from domain_kg.materials.models import AddMaterial, UploadCommand
from domain_kg.retrieval.models import SearchFilters, SearchRequest, SubgraphRequest
from domain_kg.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [
    pytest.mark.storage,
    pytest.mark.skipif(
        not os.environ.get("DOMAIN_KG_MEDICAL_PDF"), reason="public medical PDF smoke is opt-in"
    ),
]


class GraphAttribute(HTMLParser):
    graph = None

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key == "data-graph":
                self.graph = json.loads(value)


def load_input():
    config = json.loads((ROOT / "config/medical-pdf-smoke.json").read_text())
    path = Path(os.environ["DOMAIN_KG_MEDICAL_PDF"]).expanduser().resolve()
    assert path.stat().st_size == config["size_bytes"] <= config["download_cap_bytes"]
    original = path.read_bytes()
    assert hashlib.sha256(original).hexdigest() == config["sha256"]
    assert original.startswith(b"%PDF-")
    return config, path, original


async def test_real_medical_pdf_and_scoped_graph(material_service, team, catalog, issuer_fixture):
    started = time.monotonic()
    config, path, original = await asyncio.to_thread(load_input)
    publishing = await make_publishing(
        material_service,
        team,
        extraction_factory=medical_batch,
        profile_overrides=config["provider_overrides"],
    )
    materials, editor, queue, coordinator, _, faults = publishing
    preparation = preparation_steps(
        queue,
        providers=coordinator.providers,
        options=ParseOptions(**config["parse"]),
        chunk_config=ChunkConfig(**config["chunks"]),
    )
    steps = preparation + extraction_steps(queue, coordinator.providers, preparation[-1])

    async def stream():
        for offset in range(0, len(original), 65536):
            yield original[offset : offset + 65536]

    upload = await materials.upload(
        editor,
        UploadCommand(
            kg_id=editor.kg_ids[0],
            media_type="application/pdf",
            size_bytes=len(original),
            sha256=config["sha256"],
            idempotency_key="medical-pdf-upload",
        ),
        stream(),
    )
    source = await materials.add(
        editor,
        AddMaterial(
            kg_id=editor.kg_ids[0],
            upload_id=upload.upload_id,
            title=config["title"] + " [controlled smoke]",
            idempotency_key="medical-pdf",
        ),
    )
    job = await queue.claim("medical-pdf-smoke")
    document = None
    for step in steps:
        checkpoint = await step.execute(job)
        await queue.checkpoint(job, checkpoint)
        if checkpoint.stage == "chunking":
            document = await SourceRepository(queue).read(job, checkpoint)
            assert len(document.chunks) <= config["max_chunks"], len(document.chunks)
    assert document is not None and document.blocks and document.chunks
    assert len(document.chunks) <= config["max_chunks"]
    blocks = {block.id: block for block in document.blocks}
    pages = {block.location.page for block in document.blocks}
    assert None not in pages and 1 <= max(pages) <= config["parse"]["max_pages"]
    spans = 0
    for chunk in document.chunks:
        for span in chunk.spans:
            block = blocks[span.location.block_id]
            assert block.text[span.start : span.end] == span.quote
            assert span.location == block.location
            spans += 1
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    assert (
        b"".join(
            [
                part
                async for part in materials.original(
                    editor, job.kg_id, source.material_id, source.version_id
                )
            ]
        )
        == original
    )
    graph = GraphService(queue)
    read = await queue.policy.resolve_access(editor.principal, [job.kg_id], Action.READ)
    filters = SearchFilters(material_ids=(source.material_id,), version_id=source.version_id)
    scope = await graph.resolver.resolve(
        read,
        SearchRequest(kg_ids=(job.kg_id,), query="diabetes", mode="graph", filters=filters),
        job.kg_id,
    )
    seed = next(
        item.entity.id
        for item in (await graph.repository.open(scope)).observations
        if item.name.casefold() == "diabetes"
    )
    view = await graph.subgraph(
        read,
        SubgraphRequest(
            kg_id=job.kg_id,
            seed_ids=(seed,),
            max_hops=1,
            max_nodes=25,
            max_edges=40,
            filters=filters,
        ),
    )
    assert {node.name.casefold() for node in view.nodes} == {"diabetes", "annual review"}
    assert view.edges and all(edge.source.material_id == source.material_id for edge in view.edges)
    identities, _, principals, _, org, _ = team
    _, state, transport, _ = issuer_fixture
    state["issuer"] = principals["owner"].issuer
    async with httpx.AsyncClient(transport=transport) as oidc:
        app = create_app(
            Settings(
                public_url="http://localhost:28080",
                oidc_issuer=state["issuer"],
                oidc_client_id="console",
                retention_config=ROOT / "config/retention.example.json",
            ),
            identity_repository=identities,
            oidc_http_client=oidc,
            catalog_service=catalog[0],
            providers=coordinator.providers,
        )
        target = (
            f"/admin/kgs/{job.kg_id}/nodes/{seed}?material_id={source.material_id}"
            f"&version_id={source.version_id}"
        )
        metadata = {
            "kg_id": str(job.kg_id),
            "material_id": str(source.material_id),
            "version_id": str(source.version_id),
            "node_id": str(seed),
            "graph_url": target,
            "pdf_bytes": len(original),
            "sha256": config["sha256"],
            "pages": len(pages),
            "blocks": len(blocks),
            "chunks": len(document.chunks),
            "verified_chunk_spans": spans,
            "graph_nodes": len(view.nodes),
            "graph_edges": len(view.edges),
            "provider_mode": "synthetic",
            "actual_model_calls": 0,
            "seconds_before_preview": round(time.monotonic() - started, 2),
        }
        async with app.router.lifespan_context(app):
            for role in ("owner", "reader", "denied"):
                ticket = await app.state.identity.sessions.create(principals[role], org)
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://localhost:28080",
                    cookies={"domain_kg_session": ticket.session_id},
                ) as client:
                    page = await client.get(target, headers={"Accept-Language": "zh-CN"})
                    if role == "denied":
                        assert page.status_code == 404 and "data-graph=" not in page.text
                        continue
                    page.raise_for_status()
                    parser = GraphAttribute()
                    parser.feed(page.text)
                    assert parser.graph and len(parser.graph["nodes"]) == 2
                    assert "节点关系图" in page.text and "neovis-2.1.0.js" in page.text
                    evidence_link = parser.graph["edges"][0]["href"]
                    assert (
                        str(source.version_id) in evidence_link and "/materials/" in evidence_link
                    )
                    assert (await client.get(evidence_link)).status_code == 200
                    assert all(
                        str(source.material_id) in node["href"] for node in parser.graph["nodes"]
                    )
            path.with_name("smoke-result.json").write_text(json.dumps(metadata, indent=2) + "\n")
            print("Medical PDF smoke: " + json.dumps(metadata), flush=True)
            if os.environ.get("DOMAIN_KG_BROWSER_PREVIEW") == "1":
                await serve(
                    app, target, metadata, principals, org, config["browser_preview_seconds"]
                )


async def serve(app, target, metadata, principals, org, lifetime):
    assert 1 <= lifetime <= 900
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=28080,
            log_level="error",
            lifespan="off",
            access_log=False,
            timeout_graceful_shutdown=5,
        )
    )

    @app.get("/__smoke__/start/{role}")
    async def start(
        role: Literal["owner", "reader"] = "owner", lang: Literal["zh-CN", "en"] = "zh-CN"
    ):
        ticket = await app.state.identity.sessions.create(principals[role], org)
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(
            "domain_kg_session", ticket.session_id, httponly=True, samesite="lax", path="/"
        )
        response.set_cookie("domain_kg_csrf", ticket.csrf, samesite="lax", path="/")
        response.set_cookie(
            "domain_kg_language", lang, httponly=True, samesite="lax", path="/admin"
        )
        return response

    @app.get("/__smoke__/fixture")
    async def fixture():
        return metadata

    @app.get("/__smoke__/finish")
    async def finish():
        server.should_exit = True
        return {"finished": True}

    smoke_routes = app.router.routes[-3:]
    del app.router.routes[-3:]
    app.router.routes[0:0] = smoke_routes
    serving = asyncio.create_task(server.serve())
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        assert server.started
        print(
            "Medical graph preview ready: http://localhost:28080/__smoke__/start/owner", flush=True
        )
        await asyncio.wait((serving,), timeout=lifetime)
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, timeout=10)
