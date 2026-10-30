"""Batch forms retain source policy, CSRF, independent outcomes and durable job controls."""

import json
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
                    path, headers={"Origin": "http://localhost:8000"}, data=data
                )
                assert result.status_code == 200
                assert result.text.count('data-outcome="submitted"') == 2
                assert result.text.count('data-outcome="failed"') == 1
                assert (await queue.get(access, receipts[0].job_id)).stage == "queued"
                replay = await browser.post(
                    path, headers={"Origin": "http://localhost:8000"}, data=data
                )
                assert (
                    replay.status_code == 200 and replay.text.count('data-outcome="submitted"') == 2
                )
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
