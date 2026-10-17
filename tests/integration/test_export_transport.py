"""Scoped browser exports and file responses use the same current owner policy."""
# ruff: noqa: F811

import csv
import io

import httpx
import pytest
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_publication import prepare, publishing, ready  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.operations.export import ExportService
from domain_kg.worker import serve

pytestmark = pytest.mark.storage


async def test_owner_exports_selected_evidence_through_console_and_reader_is_denied(
    publishing, team, catalog, issuer_fixture
):
    _, editor, queue, coordinator, _, _ = publishing
    source, original, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, original, checkpoint))
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
                headers={"Origin": "http://localhost:8000"},
                cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
            ) as client:
                ops = f"/admin/kgs/{source.kg_id}/operations"
                assert "Create scoped export" in (await client.get(ops)).text
                fields = {
                    "csrf_token": ticket.csrf,
                    "expected_published_revision": "1",
                    "format": "csv",
                    "material_ids": str(source.material_id),
                    "idempotency_key": "browser-export",
                }
                assert (
                    await client.post(ops + "/export", data=fields | {"actor": "owner"})
                ).status_code == 422
                admitted = await client.post(ops + "/export", data=fields)
                assert admitted.status_code == 303
                assert (await client.post(ops + "/export", data=fields)).headers[
                    "location"
                ] == admitted.headers["location"]
                await serve(
                    queue,
                    JobRunner(
                        queue,
                        configured_pipeline(queue, coordinator.providers),
                        operations=ExportService(queue),
                    ),
                    once=True,
                    worker_id="export-only",
                    export_only=True,
                )
                job_id = admitted.headers["location"].rsplit("/", 1)[1]
                detail = f"/admin/kgs/{source.kg_id}/exports/jobs/{job_id}"
                assert (await client.get(detail)).status_code == 200
                link = f"/api/v1/kgs/{source.kg_id}/exports/jobs/{job_id}/file"
                file = await client.get(link)
                assert file.status_code == 200 and file.headers["cache-control"] == "no-store"
                records = list(csv.DictReader(io.StringIO(file.text)))
                assert {record["kind"] for record in records} == {"node", "edge"}
                assert file.headers["content-disposition"].startswith("attachment;")
                reader = await app.state.identity.sessions.create(principals["reader"], org)
                client.cookies.set("domain_kg_session", reader.session_id)
                for endpoint in (detail, link):
                    assert (await client.get(endpoint)).status_code in (403, 404)
