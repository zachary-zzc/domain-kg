"""Removed source metadata does not reveal evidence before readiness-gated restoration."""
# ruff: noqa: F811

import httpx
import pytest
from test_entity_resolution import TEXT
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_publication import publishing  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.materials.models import AddMaterial

pytestmark = pytest.mark.storage


async def test_console_can_remove_and_reconstruct_pending_original_without_evidence_leak(
    publishing, team, catalog, issuer_fixture
):
    materials, editor, queue, coordinator, _, _ = publishing
    kg = editor.kg_ids[0]
    receipt = await materials.add(
        editor,
        AddMaterial(
            kg_id=kg,
            title="Pending restoration source",
            inline_text=TEXT,
            idempotency_key="console-restore-source",
        ),
    )
    identities, _, principals, _, org, _ = team
    _, state, transport, _ = issuer_fixture
    state["issuer"] = principals["owner"].issuer
    async with httpx.AsyncClient(transport=transport) as oidc_client:
        app = create_app(
            settings_for(state["issuer"]),
            identity_repository=identities,
            oidc_http_client=oidc_client,
            catalog_service=catalog[0],
            providers=coordinator.providers,
        )
        async with app.router.lifespan_context(app):
            ticket = await app.state.identity.sessions.create(principals["editor"], org)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
            ) as browser:
                root = f"/admin/kgs/{kg}/materials"
                pending = await browser.get(root + "/manage", params={"state": "pending"})
                assert pending.status_code == 200 and "Pending restoration source" in pending.text
                assert "urn:fixture:alpha" not in pending.text
                api = await browser.get(
                    f"/api/v1/kgs/{kg}/materials/management", params={"state": "pending"}
                )
                assert api.status_code == 200 and api.json()["items"][0]["id"] == str(
                    receipt.material_id
                )
                assert "urn:fixture:alpha" not in api.text
                removed = await browser.post(
                    root + f"/{receipt.material_id}/remove",
                    headers={"Origin": "http://localhost:8000"},
                    data={
                        "csrf_token": ticket.csrf,
                        "action": "exclude",
                        "expected_revision": "0",
                        "idempotency_key": "console-exclude-unpublished",
                    },
                )
                assert removed.status_code == 303
                assert (await browser.get(root + f"/{receipt.material_id}")).status_code == 404
                withdrawn = await browser.get(root + "/manage", params={"state": "removed"})
                assert (
                    withdrawn.status_code == 200 and "Pending restoration source" in withdrawn.text
                )
                assert "urn:fixture:alpha" not in withdrawn.text
                restored = await browser.post(
                    root + f"/{receipt.material_id}/restore",
                    headers={"Origin": "http://localhost:8000"},
                    data={
                        "csrf_token": ticket.csrf,
                        "expected_revision": "1",
                        "idempotency_key": "console-restore-unpublished",
                    },
                )
                assert restored.status_code == 303 and "/jobs/" in restored.headers["location"]
                assert not await queue.policy.material_visible(editor, kg, receipt.material_id)
                job = await queue.claim("console-restorer", kinds=("reprocess_material",))
                assert job
                await JobRunner(queue, configured_pipeline(queue, coordinator.providers)).run_job(
                    job
                )
                status = await queue.get(editor, job.id)
                assert status.stage == "ready", status.warnings
                detail = await browser.get(root + f"/{receipt.material_id}")
                assert detail.status_code == 200 and "urn:fixture:alpha" in detail.text
                reader = await app.state.identity.sessions.create(principals["reader"], org)
                browser.cookies.clear()
                browser.cookies.set("domain_kg_session", reader.session_id)
                browser.cookies.set("domain_kg_csrf", reader.csrf)
                assert (await browser.get(root + "/manage")).status_code in (403, 404)
                assert (
                    await browser.get(f"/api/v1/kgs/{kg}/materials/management")
                ).status_code in (403, 404)
