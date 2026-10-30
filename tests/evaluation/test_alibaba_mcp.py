"""Real stores and signed test OIDC; opt in to real Alibaba calls explicitly.

Default execution mocks only the remote provider. The live variant uses the
identical profile, MCP entry point, worker, publication and retrieval code.
"""

import asyncio
import json
import os
from pathlib import Path

import httpx
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401

from domain_kg.app import create_app
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.queue import JobQueue
from domain_kg.jobs.runner import JobRunner
from domain_kg.materials.models import IngestReceipt
from domain_kg.models import Namespace
from domain_kg.providers.profiles import ProviderRegistry
from domain_kg.providers.service import ProviderService
from domain_kg.retrieval.models import SearchResponse
from domain_kg.settings import Settings
from scripts.configure_alibaba import configuration, credential

pytestmark = pytest.mark.storage
TEXT = "Alpha uses Beta. Beta is part of Gamma."


@pytest.fixture
def catalog_dimensions():
    return 1024


def provider_response(request):
    payload = json.loads(request.content)
    if request.url.path.endswith("/embeddings"):
        assert payload["model"] == "text-embedding-v4"
        assert len(payload["input"]) <= 10
        return httpx.Response(
            200,
            json={
                "model": payload["model"],
                "data": [
                    {"index": i, "embedding": [1.0] + [0.0] * 1023}
                    for i in range(len(payload["input"]))
                ],
                "usage": {"prompt_tokens": 16, "total_tokens": 16},
            },
        )
    assert request.url.path.endswith("/chat/completions")
    assert payload["model"] == "qwen3.6-35b-a3b"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["enable_thinking"] is False
    sources = json.loads(payload["messages"][1]["content"])["sources"]
    assert sources == [{"id": 0, "text": TEXT}]
    content = {
        "entities": [
            {"name": name, "type": "concept", "refs": [0]}
            for name in ("Alpha", "Beta", "Gamma")
        ],
        "assertions": [
            {
                "subject": subject,
                "target": target,
                "predicate": predicate,
                "polarity": "positive",
                "confidence": 0.95,
                "refs": [0],
                "qualifiers": [],
            }
            for subject, target, predicate in ((0, 1, "uses"), (1, 2, "part_of"))
        ],
    }
    return httpx.Response(
        200,
        json={
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
        },
    )


async def test_alibaba_ingest_publish_search_over_mcp(
    material_service, catalog, team, issuer_fixture
):
    live = os.environ.get("DOMAIN_KG_ALIBABA_LIVE") == "1"
    key_path = os.environ.get("DOMAIN_KG_ALIBABA_KEY_FILE")
    key = credential(Path(key_path) if key_path else None) if live else "fixture-key"
    materials, kg = material_service
    identities, policy, principals, _, org, _ = team
    config = configuration(
        Namespace(org_id=org, kg_id=kg),
        key,
        endpoint=os.environ.get("DOMAIN_KG_ALIBABA_ENDPOINT"),
    )
    providers = ProviderService(
        policy,
        materials.router,
        ProviderRegistry(config),
        transport=None if live else httpx.MockTransport(provider_response),
    )
    queue = JobQueue(policy, materials.router)
    await queue.initialize()
    _, state, oidc_transport, sign = issuer_fixture
    state["issuer"] = principals["owner"].issuer
    settings = Settings(
        public_url="http://localhost:8000", oidc_issuer=state["issuer"], oidc_client_id="console"
    )
    report = {"provider": "live" if live else "mock", "source": TEXT, "modes": {}}
    async with httpx.AsyncClient(transport=oidc_transport) as oidc:
        app = create_app(
            settings,
            identity_repository=identities,
            oidc_http_client=oidc,
            catalog_service=catalog[0],
            providers=providers,
        )
        async with app.router.lifespan_context(app):
            # Even a credential with admin scope cannot turn a KG editor into an owner.
            token = sign(
                {"sub": "editor", "scope": "domain_kg:read domain_kg:write domain_kg:admin"}
            )
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://localhost:8000",
                headers={"Authorization": f"Bearer {token}"},
            ) as wire:
                async with Client(
                    streamable_http_client("http://localhost:8000/mcp", http_client=wire)
                ) as mcp:
                    selection = {
                        "profile_id": str(config.profiles[0].id),
                        "expected_revision": 0,
                        "idempotency_key": "alibaba-profile",
                    }
                    binding = {"org_id": str(org), "kg_id": str(kg), "command": selection}
                    denied_binding = await mcp.call_tool("domain_kg_set_provider", binding)
                    assert denied_binding.is_error
                    owner_token = sign(
                        {"sub": "owner", "scope": "domain_kg:read domain_kg:write domain_kg:admin"}
                    )
                    wire.headers["Authorization"] = "Bearer " + owner_token
                    selected = await mcp.call_tool("domain_kg_set_provider", binding)
                    assert not selected.is_error, selected
                    assert selected.structured_content["revision"] == 1
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://localhost:8000",
                        headers={
                            "Authorization": "Bearer " + owner_token,
                            "x-domain-kg-organization": str(org),
                        },
                    ) as http:
                        replay = await http.put(f"/api/v1/kgs/{kg}/provider", json=selection)
                        assert replay.status_code == 200
                        assert replay.json() == selected.structured_content
                        stale = await http.put(
                            f"/api/v1/kgs/{kg}/provider",
                            json={**selection, "idempotency_key": "stale-selection"},
                        )
                        assert stale.status_code == 409
                        http.headers["Authorization"] = "Bearer " + token
                        refused = await http.put(f"/api/v1/kgs/{kg}/provider", json=selection)
                        assert refused.status_code in (403, 404)
                    wire.headers["Authorization"] = "Bearer " + token
                    admitted = await mcp.call_tool(
                        "domain_kg_add_material",
                        {
                            "org_id": str(org),
                            "command": {
                                "kg_id": str(kg),
                                "title": "Public Alibaba smoke fixture",
                                "inline_text": TEXT,
                                "idempotency_key": "alibaba-smoke",
                            },
                        },
                    )
                    assert not admitted.is_error, admitted
                    receipt = IngestReceipt.model_validate(admitted.structured_content)
                    job = await queue.claim("alibaba-smoke-worker")
                    assert job and job.id == receipt.job_id
                    await JobRunner(queue, configured_pipeline(queue, providers)).run_job(job)
                    status = await mcp.call_tool(
                        "domain_kg_get_job",
                        {"org_id": str(org), "kg_id": str(kg), "job_id": str(job.id)},
                    )
                    assert not status.is_error, status
                    assert status.structured_content["stage"] in ("ready", "ready_with_warnings"), (
                        status.structured_content
                    )
                    before_search = await policy.database.provider_usage.find_one(
                        {"org_id": str(org), "kg_id": str(kg)}
                    )
                    for mode in ("fast", "deep"):
                        args = {
                            "org_id": str(org),
                            "command": {
                                "kg_ids": [str(kg)], "query": "What does Alpha use?", "mode": mode
                            },
                        }
                        result = await mcp.call_tool("domain_kg_search", args)
                        assert not result.is_error, result
                        found = SearchResponse.model_validate(result.structured_content)
                        assert found.status == "ok" and found.kg_revisions[kg] == 1
                        assert found.results and found.results[0].text == TEXT
                        assert all(
                            item.material_id == receipt.material_id for item in found.results
                        )
                        assert all(item.citation_ids for item in found.results)
                        graph = next(s for s in found.branch_status if s.branch == "graph")
                        assert graph.status == ("skipped" if mode == "fast" else "ok")
                        if mode == "deep":
                            assert any(item.graph_paths for item in found.results)
                        report["modes"][mode] = found.model_dump(mode="json")
                    # A valid signed identity with no KG grant cannot retrieve the fixture.
                    wire.headers["Authorization"] = "Bearer " + sign({"sub": "denied"})
                    denied = await mcp.call_tool("domain_kg_search", args)
                    assert denied.is_error
                    assert denied.structured_content["code"] == "RESOURCE_NOT_ACCESSIBLE"
                    report["unauthorized_denied"] = True
    usage = await policy.database.provider_usage.find_one({"org_id": str(org), "kg_id": str(kg)})
    assert usage and usage["calls"] > 0
    assert usage["calls"] - usage["embedding_calls"] == (
        before_search["calls"] - before_search["embedding_calls"]
    )
    report["usage"] = {
        k: usage[k]
        for k in (
            "calls", "input_tokens", "output_tokens", "embedding_calls", "embedding_input_tokens"
        )
    }
    report["model"] = config.profiles[0].extraction_model
    report["embedding_model"] = config.profiles[0].embedding_model
    report["passed"] = True
    if live:
        output = Path(".cache/alibaba-35b/live-mcp-smoke.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(output.write_text, json.dumps(report, indent=2) + "\n")
        await asyncio.to_thread(output.chmod, 0o600)
    print(json.dumps({k: v for k, v in report.items() if k not in ("source", "modes")}))
