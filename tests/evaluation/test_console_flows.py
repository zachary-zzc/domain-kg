"""Tiny team acceptance uses genuine server sessions, current policy and real stores."""
# ruff: noqa: F811

from uuid import uuid4

import httpx
import pytest
from test_curation import alpha_id
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_publication import prepare, publishing, ready  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app
from domain_kg.catalog.backends import invite_operator
from domain_kg.identity.models import Principal

pytestmark = pytest.mark.storage


async def test_team_roles_namespace_csrf_and_revoked_session_acceptance(
    publishing, team, catalog, issuer_fixture
):
    _, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    seed = await alpha_id(queue, editor, job.kg_id)
    identities, _, principals, members, org, _ = team
    await identities.admit(
        members["owner"],
        Principal(issuer=principals["owner"].issuer, subject="console-admin-without-kg"),
        role="admin",
        idempotency_key="evaluation-admin",
    )
    principals = dict(principals) | {
        "admin": Principal(issuer=principals["owner"].issuer, subject="console-admin-without-kg")
    }
    await invite_operator(queue.database, principals["denied"].issuer, principals["denied"].subject)
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
            for role in ("owner", "editor", "reader", "denied", "admin"):
                ticket = await app.state.identity.sessions.create(principals[role], org)
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://localhost:8000",
                    headers={"Origin": "http://localhost:8000"},
                    cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
                ) as browser:
                    root = f"/admin/kgs/{job.kg_id}"
                    has_kg = role in ("owner", "editor", "reader")
                    search = await browser.get(f"{root}/search", params={"q": "Alpha"})
                    assert search.status_code == (200 if has_kg else 404), role
                    if has_kg:
                        assert "Alpha" in search.text and "/admin/citations/" in search.text
                        assert "Skip to content" in search.text and 'tabindex="-1"' in search.text
                    else:
                        assert "Alpha" not in search.text
                    graph_page = await browser.get(f"{root}/nodes/{seed}")
                    assert graph_page.status_code == (200 if has_kg else 404), role
                    if has_kg:
                        assert (
                            "data-graph=" in graph_page.text
                            and "Relationship graph" in graph_page.text
                        )
                    else:
                        assert (
                            "data-graph=" not in graph_page.text and "Alpha" not in graph_page.text
                        )
                    team_page = await browser.get("/admin/team")
                    assert (team_page.status_code == 200) == (role in ("owner", "admin")), role
                    operator = await browser.get("/admin/infrastructure")
                    assert (operator.status_code == 200) == (role in ("owner", "denied")), role
                    operations = await browser.get(f"{root}/operations")
                    assert (operations.status_code == 200) == (role == "owner"), role
                    metrics = await browser.get(f"/api/v1/kgs/{job.kg_id}/operations/metrics")
                    assert (metrics.status_code == 200) == (role == "owner"), role
                    if role == "owner":
                        assert metrics.json()["kg_id"] == str(job.kg_id)
                        assert metrics.headers["cache-control"] == "no-store"
                        assert "Search index update jobs" in operations.text
                        chinese = await browser.get(
                            f"{root}/operations", headers={"Accept-Language": "zh-CN"}
                        )
                        assert chinese.status_code == 200 and "检索索引更新任务" in chinese.text
                    upload = await browser.get(f"{root}/materials/new")
                    assert (upload.status_code == 200) == (role in ("owner", "editor")), role
                    missing = await browser.get(
                        f"/admin/kgs/{uuid4()}/materials/{source.material_id}"
                    )
                    assert missing.status_code == 404 and "Publication fixture" not in missing.text
                    forged = await browser.post(
                        f"{root}/materials/{source.material_id}/remove",
                        headers={"Origin": "https://foreign.example"},
                        data={
                            "csrf_token": ticket.csrf,
                            "action": "delete",
                            "expected_revision": "1",
                            "idempotency_key": f"evaluation-forged-{role}",
                        },
                    )
                    assert forged.status_code == 403
                    await app.state.identity.sessions.revoke(ticket.session_id)
                    revoked = await browser.get(f"{root}/search", params={"q": "Alpha"})
                    assert revoked.status_code == 401 and "Alpha" not in revoked.text
                    assert revoked.headers["cache-control"] == "no-store"
