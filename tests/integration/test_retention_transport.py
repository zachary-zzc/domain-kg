"""Owner console/API share retention intent, CSRF, replay and serial worker execution."""
# ruff: noqa: F811

import httpx
import pytest
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.operations.retention import RetentionService
from domain_kg.worker import serve

pytestmark = pytest.mark.storage


async def test_owner_retention_controls_require_explicit_resources_and_real_worker(
    material_service, team, catalog, issuer_fixture
):
    _, kg = material_service
    identities, _, principals, _, org, _ = team
    _, state, transport, _ = issuer_fixture
    state["issuer"] = principals["owner"].issuer
    async with httpx.AsyncClient(transport=transport) as oidc:
        app = create_app(
            settings_for(state["issuer"]),
            identity_repository=identities,
            oidc_http_client=oidc,
            catalog_service=catalog[0],
        )
        async with app.router.lifespan_context(app):
            runtime = app.state.services
            ticket = await app.state.identity.sessions.create(principals["owner"], org)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                headers={"Origin": "http://localhost:8000", "x-csrf-token": ticket.csrf},
                cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
            ) as browser:
                root = f"/admin/kgs/{kg}/operations"
                page = await browser.get(root)
                assert page.status_code == 200 and "Clean expired packages" in page.text
                fields = {
                    "csrf_token": ticket.csrf,
                    "expected_published_revision": "0",
                    "resources": "exports",
                    "idempotency_key": "console-cleanup",
                }
                assert (
                    await browser.post(root + "/retention", data=fields | {"csrf_token": "wrong"})
                ).status_code == 403
                missing = dict(fields)
                missing.pop("resources")
                assert (await browser.post(root + "/retention", data=missing)).status_code == 422
                queued = await browser.post(root + "/retention", data=fields)
                assert queued.status_code == 303
                api = f"/api/v1/kgs/{kg}/retention"
                command = {
                    "kg_id": str(kg),
                    "expected_published_revision": 0,
                    "resources": "exports",
                    "idempotency_key": "console-cleanup",
                }
                replay = await browser.post(api, json=command)
                assert replay.status_code == 202
                assert replay.json()["job_id"] == queued.headers["location"].rsplit("/", 1)[1]
                assert (
                    await browser.post(api, json=command | {"path": "arbitrary"})
                ).status_code == 422
                await serve(
                    runtime.queue,
                    JobRunner(
                        runtime.queue,
                        configured_pipeline(runtime.queue, runtime.providers),
                        operations=RetentionService(runtime.queue),
                    ),
                    once=True,
                    worker_id="retention-console",
                    retention_only=True,
                )
                result = await browser.get(queued.headers["location"])
                assert result.status_code == 200 and 'data-stage="ready"' in result.text
                for role in ("reader", "editor"):
                    denied = await app.state.identity.sessions.create(principals[role], org)
                    browser.cookies.set("domain_kg_session", denied.session_id)
                    browser.cookies.set("domain_kg_csrf", denied.csrf)
                    browser.headers["x-csrf-token"] = denied.csrf
                    assert (await browser.post(api, json=command)).status_code in (403, 404)
                    assert (await browser.get(root)).status_code in (403, 404)
