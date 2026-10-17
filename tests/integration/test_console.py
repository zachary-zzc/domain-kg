"""The browser console uses current server-side sessions and the shared services."""
# ruff: noqa: F811

import httpx
import pytest
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_publication import prepare, publishing, ready  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app

pytestmark = pytest.mark.storage


async def test_console_login_catalog_session_revocation_and_private_headers(
    team, catalog, issuer_fixture
):
    identities, _, principals, members, org, kg = team
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
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
            ) as browser:
                landing = await browser.get("/admin")
                assert landing.status_code == 200 and "/auth/login" in landing.text
                assert landing.headers["cache-control"] == "no-store"
                ticket = await app.state.identity.sessions.create(principals["reader"], org)
                browser.cookies.set("domain_kg_session", ticket.session_id)
                browser.cookies.set("domain_kg_csrf", ticket.csrf)
                catalog_page = await browser.get("/admin")
                assert catalog_page.status_code == 200 and str(kg) in catalog_page.text
                assert "Skip to content" in catalog_page.text
                assert catalog_page.headers["cache-control"] == "no-store"
                assert "frame-ancestors 'none'" in catalog_page.headers["content-security-policy"]
                assert 'hx-history="false"' in catalog_page.text
                switched = await browser.get("/admin/language/zh-CN", params={"next": "/admin"})
                assert switched.status_code == 303
                translated = await browser.get("/admin")
                assert translated.status_code == 200 and "知识图谱" in translated.text
                assert translated.headers["content-language"] == "zh-CN"
                assert str(kg) in translated.text and "跳至正文" in translated.text
                invalid = await browser.get("/admin/language/not-a-language")
                assert invalid.status_code == 422 and "请求字段无效" in invalid.text
                await identities.database.members.update_one(
                    {"_id": str(members["reader"].id)},
                    {"$set": {"suspended": True}},
                )
                revoked = await browser.get(f"/admin/kgs/{kg}")
                assert revoked.status_code in (401, 403) and str(kg) not in revoked.text
                assert revoked.headers["cache-control"] == "no-store"
                assert revoked.headers["content-language"] == "zh-CN"
                assert "诊断详情（英文）" in revoked.text


async def test_console_logout_csrf_and_untrusted_catalog_labels(team, catalog, issuer_fixture):
    identities, _, principals, _, org, kg = team
    _, state, transport, _ = issuer_fixture
    state["issuer"] = principals["owner"].issuer
    await identities.database.kgs.update_one(
        {"org_id": str(org), "id": str(kg)}, {"$set": {"name": "<script>private-label</script>"}}
    )
    async with httpx.AsyncClient(transport=transport) as oidc_client:
        app = create_app(
            settings_for(state["issuer"]),
            identity_repository=identities,
            oidc_http_client=oidc_client,
            catalog_service=catalog[0],
        )
        async with app.router.lifespan_context(app):
            ticket = await app.state.identity.sessions.create(principals["reader"], org)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
            ) as browser:
                page = await browser.get("/admin")
                assert page.status_code == 200
                assert "&lt;script&gt;private-label&lt;/script&gt;" in page.text
                assert "<script>private-label" not in page.text
                assert (
                    await browser.post("/admin/logout", data={"csrf_token": ""})
                ).status_code == 403
                assert (
                    await browser.post(
                        "/admin/logout",
                        headers={"Origin": "https://foreign.example"},
                        data={"csrf_token": ticket.csrf},
                    )
                ).status_code == 403
                duplicate = await browser.post(
                    "/admin/logout",
                    headers={
                        "Origin": "http://localhost:8000",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    content=f"csrf_token={ticket.csrf}&csrf_token={ticket.csrf}",
                )
                assert duplicate.status_code == 422 and ticket.csrf not in duplicate.text
                assert (await browser.get("/admin")).status_code == 200
                logged_out = await browser.post(
                    "/admin/logout",
                    headers={"Origin": "http://localhost:8000"},
                    data={"csrf_token": ticket.csrf},
                )
                assert logged_out.status_code == 303 and logged_out.headers["location"] == "/admin"
                from domain_kg.errors import DomainError

                with pytest.raises(DomainError):
                    await app.state.identity.sessions.authenticate_session(ticket.session_id)
