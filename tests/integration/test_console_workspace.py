"""The browser console uses current server-side sessions and the shared services."""
# ruff: noqa: F811

import html
import re

import httpx
import pytest
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_publication import prepare, publishing, ready  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app
from domain_kg.identity.models import Action
from domain_kg.materials.lifecycle import MaterialLifecycle
from domain_kg.materials.models import RemoveMaterial

pytestmark = pytest.mark.storage


async def test_console_reader_search_citation_and_forged_write_denial(
    publishing, team, catalog, issuer_fixture
):
    _, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
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
            ticket = await app.state.identity.sessions.create(principals["reader"], org)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
            ) as browser:
                page = await browser.get(f"/admin/kgs/{job.kg_id}/search", params={"q": "Alpha"})
                assert page.status_code == 200 and "Alpha" in page.text
                assert "/admin/citations/" in page.text
                link = re.search(r'href="(/admin/citations/[^\"]+)"', page.text)
                assert link
                cited = await browser.get(html.unescape(link.group(1)))
                assert cited.status_code == 200 and "Alpha" in cited.text
                listing = await browser.get(f"/admin/kgs/{job.kg_id}/materials")
                assert listing.status_code == 200 and "Publication fixture" in listing.text
                detail = await browser.get(f"/admin/kgs/{job.kg_id}/materials/{source.material_id}")
                assert detail.status_code == 200
                assert 'data-action="remove-material"' not in detail.text
                forged = await browser.post(
                    f"/admin/kgs/{job.kg_id}/materials/{source.material_id}/remove",
                    headers={"Origin": "http://localhost:8000"},
                    data={
                        "csrf_token": ticket.csrf,
                        "action": "delete",
                        "expected_revision": "1",
                        "idempotency_key": "reader-console-remove",
                    },
                )
                api_denied = await browser.post(
                    f"/api/v1/kgs/{job.kg_id}/materials/{source.material_id}/remove",
                    headers={"Origin": "http://localhost:8000", "x-csrf-token": ticket.csrf},
                    json={
                        "kg_id": str(job.kg_id),
                        "material_id": str(source.material_id),
                        "action": "delete",
                        "expected_revision": 1,
                        "idempotency_key": "reader-console-remove",
                    },
                )
                assert forged.status_code == api_denied.status_code in (403, 404)
                assert api_denied.json()["code"] in forged.text
                assert await queue.policy.material_visible(editor, job.kg_id, source.material_id)
                owner = await queue.policy.resolve_access(
                    principals["owner"], [job.kg_id], Action.MANAGE_GRANTS
                )
                await queue.policy.set_material_acl(
                    owner,
                    job.kg_id,
                    source.material_id,
                    "restricted",
                    member_ids=(),
                    group_ids=(),
                    idempotency_key="restrict-console-source",
                )
                denied = await browser.get(f"/admin/kgs/{job.kg_id}/materials/{source.material_id}")
                assert denied.status_code == 404 and "Publication fixture" not in denied.text


async def test_source_withdrawal_after_search_fences_rendered_evidence(
    publishing, team, catalog, issuer_fixture, monkeypatch
):
    materials, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
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
            original = app.state.services.search.search

            async def withdrawn(access, command):
                result = await original(access, command)
                await MaterialLifecycle(materials, queue).remove(
                    editor,
                    RemoveMaterial(
                        kg_id=job.kg_id,
                        material_id=source.material_id,
                        action="exclude",
                        expected_revision=1,
                        idempotency_key="withdraw-after-console-search",
                    ),
                )
                return result

            monkeypatch.setattr(app.state.services.search, "search", withdrawn)
            ticket = await app.state.identity.sessions.create(principals["reader"], org)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
            ) as browser:
                result = await browser.get(f"/admin/kgs/{job.kg_id}/search", params={"q": "Alpha"})
                assert result.status_code == 409 and "POLICY_CHANGED" in result.text
                assert "Alpha" not in result.text
