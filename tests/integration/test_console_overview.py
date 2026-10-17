"""The graph overview uses the same current source permissions as node views."""
# ruff: noqa: F811

import json
from html.parser import HTMLParser
from uuid import uuid4

import httpx
import pytest
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_publication import prepare, publishing, ready  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app

pytestmark = pytest.mark.storage


async def test_source_scoped_overview_is_bilingual_and_denies_nonmembers(
    publishing, team, catalog, issuer_fixture
):
    _, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
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
            for role in ("owner", "reader", "denied"):
                ticket = await app.state.identity.sessions.create(principals[role], org)
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://localhost:8000",
                    cookies={"domain_kg_session": ticket.session_id},
                ) as browser:
                    url = f"/admin/kgs/{job.kg_id}/graph"
                    params = {
                        "material_id": str(source.material_id),
                        "version_id": str(source.version_id),
                    }
                    for language in ("en", "zh-CN"):
                        result = await browser.get(
                            url, params=params, headers={"Accept-Language": language}
                        )
                        if role == "denied":
                            assert result.status_code == 404 and "data-graph=" not in result.text
                            continue
                        result.raise_for_status()

                        class Payload(HTMLParser):
                            value = None

                            def handle_starttag(self, tag, attrs):
                                for key, value in attrs:
                                    if key == "data-graph":
                                        self.value = json.loads(value)

                        parser = Payload()
                        parser.feed(result.text)
                        assert {n["name"] for n in parser.value["nodes"]} == {"Alpha", "Beta"}
                        assert len(parser.value["edges"]) == 1
                        assert all(
                            str(source.version_id) in n["href"] for n in parser.value["nodes"]
                        )
                        assert result.headers["content-language"] == language
                    if role != "denied":
                        hidden = await browser.get(url, params={"material_id": str(uuid4())})
                        assert "Alpha" not in hidden.text and "Beta" not in hidden.text
