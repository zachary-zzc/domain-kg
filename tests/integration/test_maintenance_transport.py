"""Current owners can queue a no-model curation repair through the shared routes."""
# ruff: noqa: F811

import httpx
import pytest
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_maintenance_admission import accepted_human_edit
from test_publication import publishing  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app
from domain_kg.graph.service import GraphService
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.worker import serve

pytestmark = pytest.mark.storage


async def test_console_curation_repair_current_owner_csrf_and_actual_worker(
    publishing, team, catalog, issuer_fixture
):
    from domain_kg.operations.maintenance import MaintenanceService

    source, alpha, curation = await accepted_human_edit(publishing)
    _, editor, queue, coordinator, _, _ = publishing
    identities, _, principals, _, org, _ = team
    _, state, transport, _ = issuer_fixture
    state["issuer"] = principals["owner"].issuer
    async with httpx.AsyncClient(transport=transport) as oidc:
        app = create_app(
            settings_for(state["issuer"]),
            identity_repository=identities,
            oidc_http_client=oidc,
            catalog_service=catalog[0],
            providers=coordinator.providers,
        )
        async with app.router.lifespan_context(app):
            ticket = await app.state.identity.sessions.create(principals["owner"], org)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                headers={"Origin": "http://localhost:8000", "x-csrf-token": ticket.csrf},
                cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
            ) as browser:
                root = f"/admin/kgs/{source.kg_id}/operations"
                page = await browser.get(root)
                assert page.status_code == 200 and "Repair accepted human projections" in page.text
                fields = {
                    "csrf_token": ticket.csrf,
                    "expected_published_revision": "2",
                    "curation_id": str(curation.revision_id),
                    "idempotency_key": "console-human-repair",
                }
                assert (
                    await browser.post(
                        root + "/repair-curation", data=fields | {"csrf_token": "wrong"}
                    )
                ).status_code == 403
                admitted = await browser.post(root + "/repair-curation", data=fields)
                assert admitted.status_code == 303
                api_command = {
                    "kg_id": str(source.kg_id),
                    "expected_published_revision": 2,
                    "curation_id": str(curation.revision_id),
                    "idempotency_key": "console-human-repair",
                }
                replay = await browser.post(
                    f"/api/v1/kgs/{source.kg_id}/repairs/curations", json=api_command
                )
                assert (
                    replay.status_code == 202
                    and str(replay.json()["job_id"])
                    == admitted.headers["location"].rsplit("/", 1)[1]
                )
                cancelled = await browser.post(
                    admitted.headers["location"] + "/cancel",
                    data={"csrf_token": ticket.csrf, "idempotency_key": "cancel-human-repair"},
                )
                assert cancelled.status_code == 303
                retried = await browser.post(
                    f"/api/v1/kgs/{source.kg_id}/jobs/{replay.json()['job_id']}/retry",
                    json={"idempotency_key": "retry-human-repair"},
                )
                assert retried.status_code == 202
                await serve(
                    queue,
                    JobRunner(
                        queue,
                        configured_pipeline(queue, coordinator.providers),
                        operations=MaintenanceService(queue),
                    ),
                    once=True,
                    worker_id="console-maintenance",
                    maintenance_only=True,
                )
                completed = await browser.get(admitted.headers["location"])
                assert completed.status_code == 200 and 'data-stage="ready"' in completed.text
                assert (
                    await GraphService(queue).get_node(editor, source.kg_id, alpha)
                ).name == "Reviewed Alpha"
                command = {
                    "kg_id": str(source.kg_id),
                    "expected_published_revision": 3,
                    "curation_id": str(curation.revision_id),
                    "idempotency_key": "reader-must-not-repair",
                }
                reader = await app.state.identity.sessions.create(principals["reader"], org)
                browser.cookies.set("domain_kg_session", reader.session_id)
                browser.cookies.set("domain_kg_csrf", reader.csrf)
                browser.headers["x-csrf-token"] = reader.csrf
                assert (
                    await browser.post(
                        f"/api/v1/kgs/{source.kg_id}/repairs/curations", json=command
                    )
                ).status_code in (403, 404)
                assert (await browser.get(root)).status_code in (403, 404)
