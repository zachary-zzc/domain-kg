"""The parser form saves only bounded operator-approved settings, without model calls."""

import httpx
import pytest
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_provider_checks import checked_provider as checked_provider  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app
from domain_kg.ingestion.configuration import ParserConfigFile

pytestmark = pytest.mark.storage


async def test_parser_advanced_form_validation_revisions_csrf_and_operator_access(
    checked_provider, team, catalog, issuer_fixture, tmp_path
):
    providers, _, kg, _, _ = checked_provider
    identities, _, principals, _, org, _ = team
    _, state, transport, _ = issuer_fixture
    state["issuer"] = principals["owner"].issuer
    path = tmp_path / "parser.json"
    settings = settings_for(state["issuer"]).model_copy(update={"parser_config": path})
    async with httpx.AsyncClient(transport=transport) as oidc:
        app = create_app(
            settings,
            identity_repository=identities,
            oidc_http_client=oidc,
            catalog_service=catalog[0],
            providers=providers,
        )
        async with app.router.lifespan_context(app):
            ticket = await app.state.identity.sessions.create(principals["owner"], org)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                headers={"Origin": "http://localhost:8000"},
                cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
            ) as browser:
                root = f"/admin/kgs/{kg}/settings"
                page = await browser.get(root + "/advanced")
                assert page.status_code == 200 and 'name="memory_limit_mib"' in page.text
                assert 'value="1024"' in page.text and "all KGs" in page.text
                fields = {
                    "csrf_token": ticket.csrf,
                    "expected_revision": "0",
                    "memory_limit_mib": "1536",
                    "timeout_seconds": "150",
                    "max_pages": "250",
                    "max_expanded_mib": "64",
                    "ocr": "disabled",
                }
                saved = await browser.post(root + "/parser", data=fields)
                assert saved.status_code == 303, saved.text
                accepted = ParserConfigFile(path).read()
                assert accepted.revision == 1
                assert accepted.options.memory_limit_mib == 1536
                assert accepted.options.timeout_seconds == 150 and not accepted.options.ocr
                assert accepted.options.max_pages == 250
                assert accepted.options.max_expanded_bytes == 64 * 1024 * 1024
                page = await browser.get(root + "/advanced")
                assert 'value="1536"' in page.text and 'value="150"' in page.text
                assert (await browser.post(root + "/parser", data=fields)).status_code == 303
                before = path.read_bytes()
                for changes in [
                    {"memory_limit_mib": "0"},
                    {"memory_limit_mib": "8193"},
                    {"max_expanded_mib": "101"},
                    {"ocr": "bad"},
                    {"server_path": "/private/source"},
                ]:
                    denied = await browser.post(root + "/parser", data=fields | changes)
                    assert denied.status_code in (400, 422), denied.text
                    assert path.read_bytes() == before
                stale = await browser.post(
                    root + "/parser", data=fields | {"memory_limit_mib": "2048"}
                )
                assert stale.status_code == 409
                denied = await browser.post(
                    root + "/parser", data=fields, headers={"Origin": "https://untrusted.example"}
                )
                assert denied.status_code == 403
                editor = await app.state.identity.sessions.create(principals["editor"], org)
                browser.cookies.set("domain_kg_session", editor.session_id)
                browser.cookies.set("domain_kg_csrf", editor.csrf)
                denied = await browser.post(
                    root + "/parser", data=fields | {"csrf_token": editor.csrf}
                )
                assert denied.status_code in (403, 404)
                assert path.read_bytes() == before
                assert (
                    await providers.database.provider_usage.count_documents({"kg_id": str(kg)}) == 0
                )


async def test_agent_parser_settings_share_the_advanced_form_policy(
    checked_provider, team, catalog, issuer_fixture, tmp_path
):
    from test_mcp_management import management_client

    providers, _, kg, _, _ = checked_provider
    async with management_client(providers, catalog, team, issuer_fixture) as (
        call,
        wire,
        sign,
        services,
    ):
        services.parser_setup.store = ParserConfigFile(tmp_path / "agent-parser.json")
        initial = await call("get_parser_settings", kg_id=str(kg))
        assert (
            not initial.is_error
            and initial.structured_content["options"]["memory_limit_mib"] == 1024
        )
        saved = await call(
            "configure_parser",
            kg_id=str(kg),
            options={"memory_limit_mib": 2048},
            expected_revision=0,
        )
        assert not saved.is_error and saved.structured_content["revision"] == 1
        invalid = await call(
            "configure_parser", kg_id=str(kg), options={"memory_limit_mib": 0}, expected_revision=1
        )
        assert invalid.is_error
        wire.headers["Authorization"] = "Bearer " + sign(
            {"sub": "reader", "scope": "domain_kg:read domain_kg:write domain_kg:admin"}
        )
        denied = await call(
            "configure_parser",
            kg_id=str(kg),
            options={"memory_limit_mib": 4096},
            expected_revision=1,
        )
        assert (
            denied.is_error and services.parser_setup.store.read().options.memory_limit_mib == 2048
        )
