import httpx
import pytest
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_provider_checks import checked_provider as checked_provider  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app

pytestmark = pytest.mark.storage


async def test_advanced_settings_defaults_hello_checks_failed_save_and_secret_preservation(
    checked_provider, team, catalog, issuer_fixture
):
    providers, access, kg, state, _ = checked_provider
    identities, _, principals, _, org, _ = team
    _, oidc_state, transport, _ = issuer_fixture
    oidc_state["issuer"] = principals["owner"].issuer
    async with httpx.AsyncClient(transport=transport) as oidc:
        app = create_app(
            settings_for(oidc_state["issuer"]),
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
                headers={"Origin": "http://localhost:8000", "Accept": "application/json"},
                cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
            ) as browser:
                root = f"/admin/kgs/{kg}/settings"
                advanced = await browser.get(root + "/advanced")
                assert advanced.status_code == 200
                assert "Advanced settings" in advanced.text
                assert "Claude" in advanced.text and "Ollama" in advanced.text
                assert "private-probe-key" not in advanced.text
                assert (
                    await providers.database.provider_usage.count_documents({"kg_id": str(kg)}) == 0
                )
                selection = await providers.selected(access, kg)
                default = await browser.post(
                    root + "/default",
                    data={"csrf_token": ticket.csrf, "profile_id": selection["profile_id"]},
                )
                assert default.status_code == 303
                assert str(providers.registry.default_profile_id) == selection["profile_id"]
                created = await browser.get("/admin/kgs/new")
                assert f'value="{selection["profile_id"]}" selected' in created.text
                hello = await browser.post(root + "/check", data={"csrf_token": ticket.csrf})
                assert hello.status_code == 200 and hello.json()["ready"]
                assert hello.json()["budget"]["calls"] == 2
                assert hello.json()["budget"]["input"] == 9
                assert hello.json()["budget"]["output"] == 6
                command = {
                    "csrf_token": ticket.csrf,
                    "name": "Changed settings",
                    "endpoint": "https://probe.example/v1",
                    "api_key": "",
                    "credential_profile_id": selection["profile_id"],
                    "embedding_profile_id": selection["profile_id"],
                    "extraction_model": "chat-test",
                    "embedding_model": "embed-test",
                    "expected_revision": "1",
                    "idempotency_key": "checked-browser-save",
                    "data_approval": "approved",
                }
                discovery = await browser.post(
                    root + "/models/chat/discover",
                    data=command
                    | {
                        "extraction_model": "",
                        "api_format": "openai",
                        "api_key": "",
                        "response_format": "json_object",
                        "thinking": "disabled",
                        "extraction_context_tokens": "16384",
                        "embedding_context_tokens": "8192",
                        "max_completion_tokens": "2048",
                        "timeout_seconds": "60",
                    },
                )
                assert discovery.status_code == 200, discovery.text
                assert "chat-test" in discovery.json()["models"]
                state["billing"] = True
                denied = await browser.post(root + "/models/test-and-save", data=command)
                assert denied.status_code == 200 and not denied.json()["ready"]
                assert denied.json()["chat"]["code"] == "PROVIDER_BILLING_REQUIRED"
                assert "private" not in denied.text
                assert (await providers.selected(access, kg))["revision"] == 1
                state["billing"] = False
                saved = await browser.post(root + "/models/test-and-save", data=command)
                assert saved.status_code == 200 and saved.json()["ready"]
                assert saved.json()["next_url"] == root + "/advanced"
                current = await providers.selected(access, kg)
                assert current["revision"] == 2
                assert (await browser.get(root + "/advanced")).status_code == 200
                assert (await browser.post(root + "/models/test-and-save", data=command)).json()[
                    "ready"
                ]
                from domain_kg.providers.usage_budget import BudgetPolicy

                providers.budgets.limits = BudgetPolicy(organization=None, knowledge_base=None)
                unlimited = await browser.get(root + "/advanced")
                assert unlimited.status_code == 200 and "Unlimited" in unlimited.text
                unlimited_check = await browser.post(
                    root + "/check", data={"csrf_token": ticket.csrf}
                )
                assert unlimited_check.json()["budget"]["call_limit"] is None
                assert unlimited_check.json()["budget"]["input_limit"] is None
                assert unlimited_check.json()["budget"]["output_limit"] is None
                editor = await app.state.identity.sessions.create(principals["editor"], org)
                browser.cookies.set("domain_kg_session", editor.session_id)
                browser.cookies.set("domain_kg_csrf", editor.csrf)
                forbidden = await browser.post(root + "/check", data={"csrf_token": editor.csrf})
                assert forbidden.status_code in (403, 404)
