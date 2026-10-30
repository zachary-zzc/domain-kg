"""Browser job controls retain shared current-policy source ownership checks."""

import httpx
import pytest
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app
from domain_kg.identity.models import Action
from domain_kg.materials.models import AddMaterial

pytestmark = pytest.mark.storage


async def test_console_pending_jobs_and_controls_use_current_source_access(
    material_service, team, catalog, issuer_fixture
):
    materials, kg = material_service
    identities, policy, principals, _, org, _ = team
    editor = await policy.resolve_access(principals["editor"], [kg], Action.INGEST)
    receipt = await materials.add(
        editor,
        AddMaterial(
            kg_id=kg,
            title="Pending source",
            inline_text="Tiny pending source.",
            idempotency_key="console-pending-job",
        ),
    )
    _, state, transport, _ = issuer_fixture
    state["issuer"] = principals["owner"].issuer
    async with httpx.AsyncClient(transport=transport) as oidc_client:
        app = create_app(
            settings_for(state["issuer"]),
            identity_repository=identities,
            oidc_http_client=oidc_client,
            catalog_service=catalog[0],
        )
        async with app.router.lifespan_context(app):
            reader = await app.state.identity.sessions.create(principals["reader"], org)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                cookies={"domain_kg_session": reader.session_id, "domain_kg_csrf": reader.csrf},
            ) as browser:
                listing = await browser.get(f"/admin/kgs/{kg}/jobs")
                assert listing.status_code == 200 and str(receipt.job_id) in listing.text
                assert "Pending source" in listing.text
                api_listing = await browser.get(f"/api/v1/kgs/{kg}/jobs")
                assert api_listing.status_code == 200
                assert api_listing.json()["items"][0]["source"]["title"] == "Pending source"
                path = f"/admin/kgs/{kg}/jobs/{receipt.job_id}"
                job = await browser.get(path)
                assert job.status_code == 200 and 'data-stage="queued"' in job.text
                assert "Pending source" in job.text and 'hx-trigger="every 3s"' in job.text
                assert 'data-action="cancel-job"' not in job.text
                denied = await browser.post(
                    path + "/cancel",
                    headers={"Origin": "http://localhost:8000"},
                    data={"csrf_token": reader.csrf, "idempotency_key": "reader-cancel"},
                )
                assert denied.status_code in (403, 404)
                ticket = await app.state.identity.sessions.create(principals["editor"], org)
                browser.cookies.clear()
                browser.cookies.set("domain_kg_session", ticket.session_id)
                browser.cookies.set("domain_kg_csrf", ticket.csrf)
                controls = await browser.get(path)
                assert 'data-action="cancel-job"' in controls.text
                cancelled = await browser.post(
                    path + "/cancel",
                    headers={"Origin": "http://localhost:8000"},
                    data={"csrf_token": ticket.csrf, "idempotency_key": "editor-cancel"},
                )
                assert cancelled.status_code == 303
                assert 'data-stage="cancelled"' in (await browser.get(path)).text
                retried = await browser.post(
                    path + "/retry",
                    headers={"Origin": "http://localhost:8000"},
                    data={"csrf_token": ticket.csrf, "idempotency_key": "editor-retry"},
                )
                assert retried.status_code == 303
                assert 'data-stage="queued"' in (await browser.get(path)).text
                owner = await policy.resolve_access(principals["owner"], [kg], Action.MANAGE_GRANTS)
                await policy.set_material_acl(
                    owner,
                    kg,
                    receipt.material_id,
                    "restricted",
                    member_ids=(),
                    group_ids=(),
                    idempotency_key="hide-console-job",
                )
                assert (await browser.get(path)).status_code == 404
                assert not (await browser.get(f"/api/v1/kgs/{kg}/jobs")).json()["items"]
