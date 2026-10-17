"""A tiny empty-KG rebuild verifies shared owner routes, CSRF and actual worker."""
# ruff: noqa: F811

from uuid import uuid4

import httpx
import pytest
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.operations.generation import GenerationService
from domain_kg.worker import serve

pytestmark = pytest.mark.storage


async def test_generation_console_api_csrf_and_serial_worker(
    material_service,
    team,
    catalog,
    issuer_fixture,
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
                assert page.status_code == 200 and "Rebuild current indexes" in page.text
                fields = {
                    "csrf_token": ticket.csrf,
                    "expected_published_revision": "0",
                    "idempotency_key": "console-generation",
                }
                assert (
                    await browser.post(root + "/reindex", data=fields | {"csrf_token": "wrong"})
                ).status_code == 403
                result = await browser.post(root + "/reindex", data=fields)
                assert result.status_code == 303
                command = {
                    "kg_id": str(kg),
                    "expected_published_revision": 0,
                    "idempotency_key": "console-generation",
                }
                replay = await browser.post(f"/api/v1/kgs/{kg}/reindex", json=command)
                assert (
                    replay.status_code == 202
                    and replay.json()["job_id"] == result.headers["location"].rsplit("/", 1)[1]
                )
                assert (
                    await browser.post(
                        f"/api/v1/kgs/{kg}/reindex", json=command | {"kg_id": str(uuid4())}
                    )
                ).status_code == 422
                assert (
                    await browser.post(
                        result.headers["location"] + "/cancel",
                        data={"csrf_token": ticket.csrf, "idempotency_key": "cancel-generation-ui"},
                    )
                ).status_code == 303
                retry = await browser.post(
                    f"/api/v1/kgs/{kg}/jobs/{replay.json()['job_id']}/retry",
                    json={"idempotency_key": "retry-generation-ui"},
                )
                assert retry.status_code == 202
                await serve(
                    runtime.queue,
                    JobRunner(
                        runtime.queue,
                        configured_pipeline(runtime.queue, runtime.providers),
                        operations=GenerationService(runtime.queue),
                    ),
                    once=True,
                    worker_id="ui-generation",
                    generation_only=True,
                )
                completed = await browser.get(result.headers["location"])
                assert completed.status_code == 200 and 'data-stage="ready"' in completed.text
                audit = await browser.get(f"/api/v1/kgs/{kg}/operations/audit")
                assert any(
                    row["kind"] == "generation" and row["outcome"] == "completed"
                    for row in audit.json()["items"]
                )
                for role in ("reader", "editor"):
                    other = await app.state.identity.sessions.create(principals[role], org)
                    browser.cookies.clear()
                    browser.cookies.set("domain_kg_session", other.session_id)
                    browser.cookies.set("domain_kg_csrf", other.csrf)
                    browser.headers["x-csrf-token"] = other.csrf
                    assert (
                        await browser.post(
                            f"/api/v1/kgs/{kg}/reindex",
                            json=command
                            | {
                                "expected_published_revision": 1,
                                "idempotency_key": role + "-generation",
                            },
                        )
                    ).status_code in (403, 404)
                    assert (await browser.get(root)).status_code in (403, 404)
