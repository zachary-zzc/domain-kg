"""Model management is operator-only, current-KG scoped, private, and usable after create."""

import json
from uuid import UUID

import httpx
import pytest
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app
from domain_kg.identity.models import Action
from domain_kg.models import Namespace
from domain_kg.providers.profiles import ProviderConfig, ProviderRegistry
from domain_kg.providers.service import ProviderService

pytestmark = pytest.mark.storage


async def test_operator_add_reuse_create_reload_and_owner_denial(
    team, catalog, issuer_fixture, tmp_path
):
    identities, policy, principals, members, org, existing_kg = team
    service, _, router, placement = catalog
    config_file = tmp_path / "providers.json"
    config_file.write_text(ProviderConfig().model_dump_json())
    config_file.chmod(0o600)
    registry = ProviderRegistry.from_file(config_file)
    worker = ProviderRegistry.from_file(config_file)
    providers = ProviderService(policy, router, registry)
    _, state, transport, _ = issuer_fixture
    state["issuer"] = principals["owner"].issuer
    async with httpx.AsyncClient(transport=transport) as oidc_client:
        app = create_app(
            settings_for(state["issuer"]),
            identity_repository=identities,
            oidc_http_client=oidc_client,
            catalog_service=service,
            providers=providers,
        )
        async with app.router.lifespan_context(app):
            owner = await app.state.identity.sessions.create(principals["owner"], org)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                headers={"Origin": "http://localhost:8000"},
                cookies={"domain_kg_session": owner.session_id, "domain_kg_csrf": owner.csrf},
            ) as browser:
                created = await browser.post(
                    "/admin/kgs/new",
                    data={
                        "name": "Model setup workspace",
                        "csrf_token": owner.csrf,
                        "placement_id": str(placement.id),
                        "domain_profile_id": str(service.general_profile_id),
                        "idempotency_key": "model-workspace",
                    },
                )
                assert created.status_code == 303
                location = created.headers["location"]
                kg = UUID(location.split("/")[3])
                page = await browser.get(location)
                assert page.status_code == 200 and "Add model configuration" in page.text
                values = {
                    "csrf_token": owner.csrf,
                    "name": "Private API <test>",
                    "endpoint": "https://chat.example/v1",
                    "api_key": "never-echo-chat-key",
                    "extraction_model": "chat-model",
                    "embedding_model": "embed-model",
                    "embedding_endpoint": "http://127.0.0.1:1234/v1",
                    "embedding_api_key": "",
                    "data_approval": "approved",
                    "expected_revision": "0",
                    "idempotency_key": "new-api",
                }
                no_approval = values.copy()
                no_approval.pop("data_approval")
                for invalid in (
                    no_approval,
                    values | {"endpoint": "https://host/v1?key=secret"},
                    values | {"config_path": "/tmp/attacker"},
                ):
                    denied = await browser.post(location + "/models", data=invalid)
                    assert denied.status_code == 422 and "never-echo" not in denied.text
                assert (
                    await browser.post(location + "/models", data=values | {"csrf_token": ""})
                ).status_code == 403
                saved = await browser.post(location + "/models", data=values)
                assert saved.status_code == 303
                assert (await browser.post(location + "/models", data=values)).status_code == 303
                selected = await identities.database.kg_provider_settings.find_one(
                    {"org_id": str(org), "kg_id": str(kg)}
                )
                profile_id = UUID(selected["profile_id"])
                profile, connection = worker.resolve(
                    profile_id, Namespace(org_id=org, kg_id=kg), restricted=False
                )
                assert connection.api_key.get_secret_value() == "never-echo-chat-key"
                assert not profile.allow_restricted
                page = await browser.get(location)
                assert "Ready for your sources" in page.text and "never-echo" not in page.text
                assert "Private API &lt;test&gt;" in page.text
                assert config_file.stat().st_mode & 0o077 == 0
                assert len(json.loads(config_file.read_text())["profiles"]) == 1
                second = await browser.post(
                    "/admin/kgs/new",
                    data={
                        "name": "Created with a model",
                        "csrf_token": owner.csrf,
                        "placement_id": str(placement.id),
                        "domain_profile_id": str(service.general_profile_id),
                        "model_profile_id": str(profile_id),
                        "idempotency_key": "new-with-model",
                    },
                )
                assert second.status_code == 303
                second_kg = UUID(second.headers["location"].split("/")[3])
                assert "setup_incomplete" not in second.headers["location"]
                worker.resolve(profile_id, Namespace(org_id=org, kg_id=second_kg), restricted=False)
                assert (
                    "Ready for your sources" in (await browser.get(second.headers["location"])).text
                )
                repeated = await browser.post(
                    "/admin/kgs/new",
                    data={
                        "name": "Created with a model",
                        "csrf_token": owner.csrf,
                        "placement_id": str(placement.id),
                        "domain_profile_id": str(service.general_profile_id),
                        "model_profile_id": str(profile_id),
                        "idempotency_key": "new-with-model",
                    },
                )
                assert repeated.headers["location"] == second.headers["location"]
                # A creation retry cannot change the selected model intent.
                changed = await browser.post(
                    "/admin/kgs/new",
                    data={
                        "name": "Created with a model",
                        "csrf_token": owner.csrf,
                        "placement_id": str(placement.id),
                        "domain_profile_id": str(service.general_profile_id),
                        "model_profile_id": "",
                        "idempotency_key": "new-with-model",
                    },
                )
                assert changed.status_code == 409
                # Original setup remains retry-safe after the profile is reused by another KG.
                assert (await browser.post(location + "/models", data=values)).status_code == 303
                # KG owners without operator privileges cannot register or approve models.
                access = await policy.resolve_access(
                    principals["owner"], [kg], Action.MANAGE_GRANTS, org_id=org
                )
                await policy.set_grant(
                    access,
                    kg,
                    "member",
                    members["reader"].id,
                    "owner",
                    idempotency_key="owner-reader",
                )
                reader = await app.state.identity.sessions.create(principals["reader"], org)
                browser.cookies.set("domain_kg_session", reader.session_id)
                browser.cookies.set("domain_kg_csrf", reader.csrf)
                assert 'id="add-model"' not in (await browser.get(location)).text
                assert (
                    await browser.post(
                        location + "/models", data=values | {"csrf_token": reader.csrf}
                    )
                ).status_code == 403
                assert (
                    await browser.post(
                        location + "/approve-model",
                        data={
                            "csrf_token": reader.csrf,
                            "profile_id": str(profile_id),
                            "data_approval": "approved",
                            "expected_revision": "1",
                            "idempotency_key": "reader-approve",
                        },
                    )
                ).status_code == 403
                # Neither raw credential nor endpoint is stored in authority audit records.
                audit = await identities.database.policy_audit.find({"kg_id": str(kg)}).to_list()
                assert "never-echo" not in repr(audit) and "chat.example" not in repr(audit)
