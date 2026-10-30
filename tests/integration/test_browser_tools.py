"""The new browser entry points enforce the existing current-session policy."""

import httpx
import pytest
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app

pytestmark = pytest.mark.storage


async def test_folder_and_mcp_pages_enforce_roles_and_do_not_embed_credentials(
    material_service, catalog, team, issuer_fixture
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
            for role in ("owner", "editor", "reader", "denied"):
                ticket = await app.state.identity.sessions.create(principals[role], org)
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://localhost:8000",
                    cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
                ) as browser:
                    folder = await browser.get(f"/admin/kgs/{kg}/folder")
                    mcp = await browser.get(f"/admin/kgs/{kg}/mcp-test")
                    assert (folder.status_code == 200) == (role in ("owner", "editor"))
                    assert (mcp.status_code == 200) == (role != "denied")
                    if mcp.status_code == 200:
                        assert "data-mcp-connect" in mcp.text
                        assert ticket.session_id not in mcp.text
                        assert 'id="mcp-token" type="password"' in mcp.text
                        assert mcp.headers["cache-control"] == "no-store"
                    if folder.status_code == 200:
                        assert "webkitdirectory" in folder.text
                        assert "frame-ancestors 'none'" in folder.headers["content-security-policy"]
