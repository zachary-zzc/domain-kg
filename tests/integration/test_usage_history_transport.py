"""Usage history is visible only through the current owner's shared routes."""
# ruff: noqa: F811

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app
from domain_kg.providers.usage_budget import BudgetCounts

pytestmark = pytest.mark.storage


async def test_usage_history_api_window_console_and_role_denials(
    material_service,
    team,
    catalog,
    issuer_fixture,
):
    _, kg = material_service
    identities, policy, principals, _, org, _ = team
    today = datetime.now(UTC).date()
    await policy.database.provider_budget_days.insert_one(
        {
            "org_id": str(org),
            "kg_id": str(kg),
            "scope": "kg",
            "day": today.isoformat(),
            "counts": BudgetCounts(spent_input_tokens=432, reserved_calls=1).model_dump(),
            "private_endpoint": "must-never-appear",
        }
    )
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
                cookies={"domain_kg_session": ticket.session_id},
            ) as browser:
                url = f"/api/v1/kgs/{kg}/usage/history"
                response = await browser.get(url)
                assert response.status_code == 200
                assert (
                    len(response.json()["items"]) == 1
                    and response.json()["items"][0]["counts"]["spent_input_tokens"] == 432
                )
                assert "must-never-appear" not in response.text and "limits" not in response.json()
                for parameters in (
                    {"days": 32},
                    {"days": 0},
                    {"end_date": "0001-01-01", "days": 31},
                    {"end_date": "2026-02-30"},
                    {"end_date": (today + timedelta(days=1)).isoformat()},
                ):
                    assert (await browser.get(url, params=parameters)).status_code == 422
                page = await browser.get(f"/admin/kgs/{kg}/operations")
                assert (
                    page.status_code == 200
                    and "Provider usage history" in page.text
                    and "432" in page.text
                )
                assert "must-never-appear" not in page.text
                for role in ("reader", "editor"):
                    other = await app.state.identity.sessions.create(principals[role], org)
                    browser.cookies.clear()
                    browser.cookies.set("domain_kg_session", other.session_id)
                    assert (await browser.get(url)).status_code in (403, 404)
