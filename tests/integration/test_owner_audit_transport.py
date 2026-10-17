"""Only current KG owners can read redacted attempt outcomes through API or console."""
# ruff: noqa: F811

import httpx
import pytest
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app
from domain_kg.errors import DomainError, ErrorCode

pytestmark = pytest.mark.storage


async def test_owner_audit_console_api_scope_and_redaction(
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
            ticket = await app.state.identity.sessions.create(principals["owner"], org)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                headers={"Origin": "http://localhost:8000", "x-csrf-token": ticket.csrf},
                cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
            ) as browser:
                submitted = await browser.post(
                    f"/api/v1/kgs/{kg}/backups",
                    json={
                        "kg_id": str(kg),
                        "expected_published_revision": 0,
                        "idempotency_key": "audit-ui-failure",
                    },
                )
                assert submitted.status_code == 202
                job = await app.state.services.queue.claim("audit-ui", kinds=("backup",))
                await app.state.services.queue.fail(
                    job, DomainError(ErrorCode.VALIDATION_ERROR, "private detailed failure")
                )
                api = f"/api/v1/kgs/{kg}/operations/audit"
                result = await browser.get(api)
                assert result.status_code == 200
                failed = next(row for row in result.json()["items"] if row["outcome"] == "failed")
                assert (
                    failed["attempt_id"] == str(job.attempt_id)
                    and failed["error_code"] == "VALIDATION_ERROR"
                )
                assert "private detailed failure" not in result.text
                assert (await browser.get(api + "?limit=101")).status_code == 422
                page = await browser.get(f"/admin/kgs/{kg}/operations")
                assert (
                    page.status_code == 200
                    and "Owner operation audit" in page.text
                    and "VALIDATION_ERROR" in page.text
                )
                for role in ("reader", "editor"):
                    denied = await app.state.identity.sessions.create(principals[role], org)
                    browser.cookies.clear()
                    browser.cookies.set("domain_kg_session", denied.session_id)
                    assert (await browser.get(api)).status_code in (403, 404)
