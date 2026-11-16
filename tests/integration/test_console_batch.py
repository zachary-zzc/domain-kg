"""Batch forms retain source policy, CSRF, independent outcomes and durable job controls."""

import json
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app
from domain_kg.identity.models import Action
from domain_kg.materials.models import AddMaterial

pytestmark = pytest.mark.storage


async def test_batch_job_retry_is_independent_idempotent_and_scoped(
    material_service, team, catalog, issuer_fixture
):
    materials, kg = material_service
    identities, policy, principals, _, org, _ = team
    access = await policy.resolve_access(principals["editor"], [kg], Action.INGEST)
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
            queue = app.state.services.queue
            receipts = []
            for i in range(2):
                receipt = await materials.add(
                    access,
                    AddMaterial(
                        kg_id=kg,
                        title=f"Bulk {i}",
                        inline_text="Synthetic batch fixture.",
                        idempotency_key=f"bulk-{i}",
                    ),
                )
                await queue.cancel(access, receipt.job_id, idempotency_key=f"stop-{i}")
                receipts.append(receipt)
            ticket = await app.state.identity.sessions.create(principals["editor"], org)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                follow_redirects=True,
                cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
            ) as browser:
                data = {
                    "csrf_token": ticket.csrf,
                    "operation": "retry",
                    "idempotency_key": "batch-retry",
                    "items": json.dumps(
                        [
                            {"id": str(receipts[0].job_id)},
                            {"id": str(uuid4())},
                            {"id": str(receipts[1].job_id)},
                        ]
                    ),
                }
                path = f"/admin/kgs/{kg}/batch"
                result = await browser.post(
                    path,
                    headers={"Origin": "http://localhost:8000"},
                    data=data,
                    follow_redirects=False,
                )
                assert result.status_code == 303
                destination = urlsplit(result.headers["location"])
                assert destination.path == f"/admin/kgs/{kg}/materials"
                query = parse_qs(destination.query)
                assert query["view"] == ["processing"]
                batch_id = query["batch_id"][0]
                legacy = await browser.get(
                    f"/admin/kgs/{kg}/batch/{batch_id}", follow_redirects=False
                )
                assert legacy.status_code == 303
                assert legacy.headers["location"] == result.headers["location"]
                result = await browser.get(result.headers["location"])
                assert result.status_code == 200
                assert result.text.count('data-outcome="submitted"') == 2
                assert result.text.count('data-outcome="failed"') == 1
                assert (await queue.get(access, receipts[0].job_id)).stage == "queued"
                replay = await browser.post(
                    path,
                    headers={"Origin": "http://localhost:8000"},
                    data=data,
                    follow_redirects=False,
                )
                assert replay.status_code == 303
                assert replay.headers["location"] == legacy.headers["location"]
                import_id = uuid4()
                redirected = await browser.post(
                    path,
                    headers={"Origin": "http://localhost:8000"},
                    data=data | {"return_view": "attention", "import_id": str(import_id)},
                    follow_redirects=False,
                )
                assert redirected.status_code == 303
                assert parse_qs(urlsplit(redirected.headers["location"]).query) == {
                    "view": ["attention"],
                    "batch_id": [batch_id],
                    "import_id": [str(import_id)],
                }
                for context in (
                    {"return_view": "https://untrusted.example"},
                    {"return_view": "other"},
                    {"import_id": "../foreign"},
                    {"return_url": "https://untrusted.example"},
                ):
                    invalid = await browser.post(
                        path,
                        headers={"Origin": "http://localhost:8000"},
                        data=data | context,
                        follow_redirects=False,
                    )
                    assert invalid.status_code == 422
                changed = await browser.post(
                    path,
                    headers={"Origin": "http://localhost:8000"},
                    data=data | {"operation": "cancel"},
                )
                assert changed.status_code == 409
                listing = await browser.get(f"/admin/kgs/{kg}/jobs")
                assert "data-bulk-form" in listing.text and "data-bulk-item" in listing.text
                bad_csrf = await browser.post(
                    path,
                    headers={"Origin": "http://localhost:8000"},
                    data=data | {"csrf_token": "wrong"},
                )
                assert bad_csrf.status_code == 403
                other_editor = await app.state.identity.sessions.create(principals["owner"], org)
                browser.cookies.clear()
                browser.cookies.set("domain_kg_session", other_editor.session_id)
                browser.cookies.set("domain_kg_csrf", other_editor.csrf)
                private = await browser.get(f"/admin/kgs/{kg}/batch/{batch_id}")
                assert private.status_code == 404
                reader = await app.state.identity.sessions.create(principals["reader"], org)
                browser.cookies.clear()
                browser.cookies.set("domain_kg_session", reader.session_id)
                browser.cookies.set("domain_kg_csrf", reader.csrf)
                denied = await browser.post(
                    path,
                    headers={"Origin": "http://localhost:8000"},
                    data=data | {"csrf_token": reader.csrf},
                )
                assert denied.status_code in (403, 404)
                assert "data-bulk-form" not in (await browser.get(f"/admin/kgs/{kg}/jobs")).text
