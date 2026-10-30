"""External identity management uses current browser sessions, CSRF and shared services."""
# ruff: noqa: F811

from uuid import UUID, uuid4

import httpx
import pytest
from test_identity_bridge import bridge as bridge  # noqa: F401
from test_identity_bridge import issuer_fixture as issuer_fixture  # noqa: F401

from domain_kg.identity.models import Principal

pytestmark = pytest.mark.storage


async def test_connection_browser_revocation_is_private_session_bound_and_csrf_protected(
    bridge, team
):
    async with bridge() as (_, app, boot, exchange, _, _, _, state):
        assert (await boot()).status_code == 200
        issued = await exchange()
        assert issued.status_code == 200, issued.text
        value = issued.json()
        user = Principal(issuer=state["issuer"], subject="new-user")
        ticket = await app.state.identity.sessions.create(user, team[4])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost:8000",
            cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
            headers={"Origin": "http://localhost:8000"},
        ) as browser:
            page = await browser.get("/admin/connections")
            assert page.status_code == 200, page.text
            assert "Trusted test platform" in page.text
            assert value["connection_id"] in page.text
            assert value["access_token"] not in page.text
            assert "synthetic-exchange-secret" not in page.text
            assert page.headers["cache-control"] == "no-store"
            assert (await browser.get("/admin/identity-integrations")).status_code in (403, 404)
            target = f"/admin/connections/{value['connection_id']}/revoke"
            fields = {
                "csrf_token": ticket.csrf,
                "expected_revision": "0",
                "idempotency_key": "browser-revoke",
            }
            denied = await browser.post(target, data=fields | {"csrf_token": "wrong"})
            assert denied.status_code == 403
            denied = await browser.post(
                target, data=fields, headers={"Origin": "https://other.example"}
            )
            assert denied.status_code == 403
            foreign = await browser.post(f"/admin/connections/{uuid4()}/revoke", data=fields)
            assert foreign.status_code == 404
            revoked = await browser.post(target, data=fields)
            assert (
                revoked.status_code == 303 and revoked.headers["location"] == "/admin/connections"
            )
            assert "Revoked" in (await browser.get("/admin/connections")).text
            assert (
                await browser.post(target, data=fields | {"org_id": str(uuid4())})
            ).status_code == 422


async def test_admin_can_review_users_and_disable_integration_without_exposing_profile_secrets(
    bridge, team
):
    async with bridge() as (_, app, boot, exchange, _, _, integration, _):
        assert (await boot()).status_code == 200
        issued = await exchange()
        assert issued.status_code == 200, issued.text
        link = await team[0].database.identity_links.find_one({"integration_id": integration})
        assert link is not None
        ticket = await app.state.identity.sessions.create(team[2]["owner"], team[4])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost:8000",
            cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
            headers={"Origin": "http://localhost:8000"},
        ) as browser:
            page = await browser.get("/admin/identity-integrations")
            assert page.status_code == 200, page.text
            assert "Synthetic platform" in page.text and "Trusted test platform" in page.text
            assert (
                "synthetic-exchange-secret" not in page.text and "caller_audience" not in page.text
            )
            users = await browser.get(f"/admin/identity-integrations/{integration}/users")
            assert users.status_code == 200 and "Withdraw approval" in users.text
            assert (
                await browser.get(f"/admin/identity-integrations/{uuid4()}/users")
            ).status_code == 404
            action = f"/admin/identity-links/{UUID(link['id'])}/approval"
            changed = await browser.post(
                action,
                data={
                    "csrf_token": ticket.csrf,
                    "expected_revision": "0",
                    "idempotency_key": "console-withdraw",
                    "approved": "false",
                },
            )
            assert changed.status_code == 303, changed.text
            assert (await exchange()).status_code == 401
            changed = await browser.post(
                action,
                data={
                    "csrf_token": ticket.csrf,
                    "expected_revision": "1",
                    "idempotency_key": "console-approve",
                    "approved": "true",
                },
            )
            assert changed.status_code == 303, changed.text
            assert (await exchange()).status_code == 200
            disabled = await browser.post(
                f"/admin/identity-integrations/{integration}",
                data={
                    "csrf_token": ticket.csrf,
                    "expected_revision": "0",
                    "idempotency_key": "console-disable",
                    "active": "false",
                },
            )
            assert disabled.status_code == 303, disabled.text
            assert (await exchange()).status_code in (401, 403, 404)
