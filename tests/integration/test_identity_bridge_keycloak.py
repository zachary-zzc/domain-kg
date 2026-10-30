"""A real pinned IdP drives provisioning, delegated MCP, publication and revocation."""

# ruff: noqa: F811
import base64
import json
import secrets

import httpx
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from test_entity_resolution import TEXT
from test_publication import publishing as publishing

from domain_kg.app import create_app
from domain_kg.identity.bridge.models import DefaultGrant, IntegrationCreate
from domain_kg.identity.models import Action
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.settings import Settings
from scripts.verify_identity_exchange import (
    RESOURCE,
    caller_token,
    isolated_keycloak,
    user_token,
)

pytestmark = pytest.mark.storage


async def test_real_keycloak_bootstrap_mcp_publication_and_revoke(
    publishing, team, catalog, tmp_path
):
    _, existing_access, _, coordinator, _, _ = publishing
    kg, org = existing_access.kg_ids[0], team[4]
    with isolated_keycloak() as proof:
        profile = proof.profile.model_copy(update={"allowed_org_ids": frozenset({org})})
        path = tmp_path / "private-identity-bridge.json"
        path.write_text(json.dumps({
            "response_key": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
            "profiles": [profile.model_dump(mode="json") | {
                "exchange_client_secret": profile.exchange_client_secret.get_secret_value()
            }],
        }))
        path.chmod(0o600)
        settings = Settings(
            public_url=RESOURCE.removesuffix("/mcp"),
            oidc_issuer=proof.issuer,
            oidc_client_id="synthetic-console",
            identity_bridge_config=path,
        )
        async with httpx.AsyncClient(
            timeout=15, trust_env=False, follow_redirects=False
        ) as provider:
            app = create_app(
                settings,
                identity_repository=team[0],
                catalog_service=catalog[0],
                oidc_http_client=provider,
                providers=coordinator.providers,
            )
            async with app.router.lifespan_context(app):
                # Fixture setup uses the existing shared Policy and owner. The
                # external platform itself cannot create its trust integration.
                admin = await team[1].resolve_access(
                    team[2]["owner"], [], Action.MANAGE_ORGANIZATION, org_id=org
                )
                integration = await app.state.identity.bridge.control.create(
                    admin,
                    IntegrationCreate(
                        profile_id=profile.id,
                        name="Synthetic real Keycloak platform",
                        allowed_kg_ids=(kg,),
                        allowed_actions=(Action.READ, Action.SEARCH, Action.INGEST),
                        default_grants=(DefaultGrant(kg_id=kg, role="editor"),),
                        auto_approve=True,
                        idempotency_key="real-idp-integration",
                    ),
                )
                caller = await caller_token(proof, provider)
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url=RESOURCE.removesuffix("/mcp"),
                ) as http:
                    bootstrap = await http.post(
                        "/api/v1/identity-bridge/bootstrap",
                        headers={
                            "Authorization": "Bearer " + caller,
                            "Idempotency-Key": "real-bootstrap",
                        },
                        json={
                            "integration_id": str(integration.id),
                            "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
                            "subject_token": await user_token(proof, provider),
                        },
                    )
                    assert bootstrap.status_code == 200, bootstrap.status_code
                    assert bootstrap.json()["status"] == "active"
                    assert bootstrap.json()["connection_allowed"]
                    result = await http.post(
                        "/api/v1/identity-bridge/token",
                        headers={
                            "Authorization": "Bearer " + caller,
                            "Idempotency-Key": "real-token",
                        },
                        json={
                            "integration_id": str(integration.id),
                            "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
                            "subject_token": await user_token(proof, provider),
                            "kg_ids": [str(kg)],
                            "actions": ["read", "search", "ingest"],
                            "connection_seconds": 3600,
                        },
                    )
                    assert result.status_code == 200, result.status_code
                    issued = result.json()
                    authorization = {"Authorization": "Bearer " + issued["access_token"]}
                    async with httpx2.AsyncClient(
                        transport=httpx2.ASGITransport(app=app),
                        base_url=RESOURCE.removesuffix("/mcp"),
                        headers=authorization,
                    ) as wire, Client(
                        streamable_http_client(RESOURCE, http_client=wire)
                    ) as mcp:
                        listed = await mcp.call_tool("domain_kg_list_kgs", {})
                        assert not listed.is_error
                        assert [item["id"] for item in listed.structured_content["items"]] == [
                            str(kg)
                        ]
                        added = await mcp.call_tool("domain_kg_add_material", {
                            "command": {
                                "kg_id": str(kg), "title": "Real IdP synthetic evidence",
                                "inline_text": TEXT, "idempotency_key": "real-idp-material",
                            },
                        })
                        assert not added.is_error
                        queue = app.state.services.queue
                        runner = JobRunner(queue, configured_pipeline(queue, coordinator.providers))
                        job = await queue.claim("real-idp-worker")
                        assert job is not None
                        enqueued = await team[0].database.jobs.find_one({"id": str(job.id)})
                        assert (
                            enqueued["actor"]["delegation"]["connection_id"]
                            == issued["connection_id"]
                        )
                        await runner.run_job(job)
                        stored = await team[0].database.jobs.find_one({"id": str(job.id)})
                        assert stored["stage"] == "ready" and stored["accepted_manifest_id"]
                        recalled = await mcp.call_tool("domain_kg_search", {
                            "command": {"kg_ids": [str(kg)], "query": "Alpha", "mode": "lexical"}
                        })
                        assert not recalled.is_error and recalled.structured_content["results"]
                        pending = await mcp.call_tool("domain_kg_add_material", {
                            "command": {
                                "kg_id": str(kg), "title": "Revoke before worker",
                                "inline_text": TEXT, "idempotency_key": "real-idp-revoked-job",
                            },
                        })
                        assert not pending.is_error
                        revoked = await http.post(
                            f"/api/v1/me/connections/{issued['connection_id']}/revoke",
                            headers=authorization,
                            json={"expected_revision": 0, "idempotency_key": "real-revoke"},
                        )
                        assert revoked.status_code == 200
                        denied = await http.get("/api/v1/me", headers=authorization)
                        assert denied.status_code == 401
                        # The accepted task keeps its delegation reference, so
                        # revoked authority cannot publish through a worker.
                        revoked_job = await queue.claim("real-idp-revoked-worker")
                        assert revoked_job is not None
                        enqueued = await team[0].database.jobs.find_one({"id": str(revoked_job.id)})
                        assert (
                            enqueued["actor"]["delegation"]["connection_id"]
                            == issued["connection_id"]
                        )
                        await runner.run_job(revoked_job)
                        rejected = await team[0].database.jobs.find_one(
                            {"id": str(revoked_job.id)}
                        )
                        assert rejected["stage"] == "failed"
                        assert not rejected.get("accepted_manifest_id")
                    # New MCP requests require authentication even after a
                    # client already completed transport initialization.
                    after = await http.post(
                        "/mcp",
                        headers=authorization | {"Accept": "application/json, text/event-stream"},
                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                    )
                    assert after.status_code == 401
