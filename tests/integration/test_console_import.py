"""Imported graph inspection and history require explicit unverified opt-in."""

import httpx
import pytest
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app

pytestmark = pytest.mark.storage


async def test_imported_node_requires_explicit_opt_in_and_keeps_honest_history(
    material_service, team, catalog, issuer_fixture
):
    from test_import_recovery import imported

    from domain_kg.revisions.publication import accepted_document

    fixture, view = await imported(material_service, team)
    _, editor, queue, coordinator, _, _ = fixture
    source = view.sources[0]
    document, _, _ = await accepted_document(queue, editor, view.projections[0])
    entity = next(row.observation.entity.id for row in document.rows if row.observation)
    identities, _, principals, _, org, _ = team
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
            ticket = await app.state.identity.sessions.create(principals["editor"], org)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                headers={"Origin": "http://localhost:8000"},
                cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
            ) as browser:
                node = f"/admin/kgs/{source.kg_id}/nodes/{entity}"
                assert (await browser.get(node)).status_code == 404
                visible = await browser.get(node, params={"include_unverified": "true"})
                assert visible.status_code == 200 and "Unverified legacy import" in visible.text
                result = await browser.post(
                    f"{node}/edit",
                    data={
                        "csrf_token": ticket.csrf,
                        "expected_revision": "0",
                        "curation_scope": "source",
                        "source_ids": str(source.material_id),
                        "tags": "reviewed",
                        "reason": "Review the imported name",
                        "idempotency_key": "console-import-tags",
                    },
                )
                assert result.status_code == 303
                hidden = await browser.get(f"{node}/history")
                assert hidden.status_code == 404
                history = await browser.get(
                    f"{node}/history", params={"include_unverified": "true"}
                )
                assert history.status_code == 200 and "Review the imported name" in history.text
                assert "Unverified legacy imports included" in history.text
