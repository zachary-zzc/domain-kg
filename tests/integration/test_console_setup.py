"""Browser creation and model setup retain catalog policy, CSRF and idempotency."""

from uuid import UUID, uuid4

import httpx
import pytest
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app
from domain_kg.models import Namespace
from domain_kg.providers.profiles import (
    ProviderConfig,
    ProviderConnection,
    ProviderProfile,
    ProviderRegistry,
)

pytestmark = pytest.mark.storage


async def test_browser_kg_delete_restore_and_rename_share_mcp_catalog_boundary(
    material_service, team, catalog, issuer_fixture
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
            ticket = await app.state.identity.sessions.create(principals["owner"], org)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                headers={"Origin": "http://localhost:8000"},
                cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
            ) as browser:
                form = await browser.get(f"/admin/kgs/{kg}/settings")
                assert form.status_code == 200 and "Delete knowledge graph" in form.text
                revision = (await browser.get(f"/api/v1/kgs/{kg}")).json()["revision"]
                deleted = await browser.post(
                    f"/admin/kgs/{kg}/lifecycle/delete",
                    data={
                        "csrf_token": ticket.csrf,
                        "expected_revision": str(revision),
                        "idempotency_key": "browser-delete-kg",
                    },
                )
                assert deleted.status_code == 303
                assert (await browser.get(f"/api/v1/kgs/{kg}")).status_code == 404
                trash = await browser.get("/admin?lifecycle=deleted")
                assert trash.status_code == 200 and str(kg) in trash.text
                settings = await browser.get(f"/admin/kgs/{kg}/settings")
                assert settings.status_code == 200 and "Restore knowledge graph" in settings.text
                restored = await browser.post(
                    f"/admin/kgs/{kg}/lifecycle/restore",
                    data={
                        "csrf_token": ticket.csrf,
                        "expected_revision": str(revision + 1),
                        "idempotency_key": "browser-restore-kg",
                    },
                )
                assert restored.status_code == 303
                assert (await browser.get(f"/api/v1/kgs/{kg}")).status_code == 200


async def test_browser_create_kg_approved_placement_owner_setup_and_denials(
    team, catalog, issuer_fixture
):
    identities, _, principals, _, org, _ = team
    service, _, _, placement = catalog
    _, state, transport, _ = issuer_fixture
    state["issuer"] = principals["owner"].issuer
    foreign = placement.model_copy(update={"id": uuid4(), "allowed_org_ids": frozenset({uuid4()})})
    await identities.database.storage_profiles.insert_one(foreign.model_dump(mode="json"))
    async with httpx.AsyncClient(transport=transport) as oidc_client:
        app = create_app(
            settings_for(state["issuer"]),
            identity_repository=identities,
            oidc_http_client=oidc_client,
            catalog_service=service,
        )
        async with app.router.lifespan_context(app):
            owner = await app.state.identity.sessions.create(principals["owner"], org)
            reader = await app.state.identity.sessions.create(principals["reader"], org)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                headers={"Origin": "http://localhost:8000"},
            ) as browser:

                def sign_in(ticket):
                    browser.cookies.set("domain_kg_session", ticket.session_id)
                    browser.cookies.set("domain_kg_csrf", ticket.csrf)

                sign_in(reader)
                listing = await browser.get("/admin")
                assert listing.status_code == 200 and 'href="/admin/kgs/new"' not in listing.text
                assert (await browser.get("/admin/kgs/new")).status_code == 403
                fields = {
                    "csrf_token": reader.csrf,
                    "name": "Console creation <test>",
                    "domain_profile_id": str(service.general_profile_id),
                    "placement_id": str(placement.id),
                    "idempotency_key": "browser-create-kg",
                }
                assert (await browser.post("/admin/kgs/new", data=fields)).status_code == 403
                sign_in(owner)
                fields["csrf_token"] = owner.csrf
                form = await browser.get("/admin/kgs/new")
                assert form.status_code == 200
                assert str(placement.id) in form.text and str(foreign.id) not in form.text
                assert "does not create new database servers" in form.text
                assert (
                    await browser.post("/admin/kgs/new", data=fields | {"csrf_token": ""})
                ).status_code == 403
                assert (
                    await browser.post(
                        "/admin/kgs/new", data=fields, headers={"Origin": "https://foreign.example"}
                    )
                ).status_code == 403
                for bad in ({"name": "  "}, {"org_id": str(uuid4())}):
                    assert (
                        await browser.post("/admin/kgs/new", data=fields | bad)
                    ).status_code == 422
                assert (
                    await browser.post(
                        "/admin/kgs/new", data=fields | {"placement_id": str(foreign.id)}
                    )
                ).status_code == 404
                created = await browser.post("/admin/kgs/new", data=fields)
                assert created.status_code == 303
                location = created.headers["location"]
                kg = UUID(location.split("/")[3])
                assert (await browser.post("/admin/kgs/new", data=fields)).headers[
                    "location"
                ] == location
                assert (
                    await identities.database.kgs.count_documents(
                        {"org_id": str(org), "name": fields["name"]}
                    )
                    == 1
                )
                configured = await browser.get(location)
                assert configured.status_code == 200
                assert "Model approval needed" in configured.text
                assert "Console creation &lt;test&gt;" in configured.text
                assert 'aria-current="page"' in configured.text
                assert (
                    await browser.post(
                        f"/admin/kgs/{kg}/settings/provider",
                        data={
                            "csrf_token": owner.csrf,
                            "profile_id": str(uuid4()),
                            "expected_revision": "0",
                            "idempotency_key": "unapproved-provider",
                        },
                    )
                ).status_code == 503
                profile = ProviderProfile(
                    id=uuid4(),
                    connection_ref="ui-test",
                    extraction_model="test-extractor",
                    embedding_model="test-embedding",
                    embedding_dimensions=catalog[2].dimensions,
                    allowed_namespaces=(Namespace(org_id=org, kg_id=kg),),
                    data_policy_approved=True,
                )
                hidden = profile.model_copy(
                    update={
                        "id": uuid4(),
                        "extraction_model": "private-other-kg-model",
                        "allowed_namespaces": (Namespace(org_id=org, kg_id=uuid4()),),
                    }
                )
                registry = ProviderRegistry(
                    ProviderConfig(
                        connections=(
                            ProviderConnection(
                                ref="ui-test",
                                endpoint="https://provider.invalid/v1",
                                api_key="fixture-key",
                            ),
                        ),
                        profiles=(profile, hidden),
                    )
                )
                app.state.services.providers.registry = registry
                choices = await browser.get(location)
                assert "test-extractor" in choices.text
                assert "private-other-kg-model" not in choices.text
                assert "provider.invalid" not in choices.text and "fixture-key" not in choices.text
                provider_fields = {
                    "csrf_token": owner.csrf,
                    "profile_id": str(profile.id),
                    "expected_revision": "0",
                    "idempotency_key": "select-approved-profile",
                }
                assert (
                    await browser.post(f"/admin/kgs/{kg}/settings/provider", data=provider_fields)
                ).status_code == 303
                assert "Ready for your sources" in (await browser.get(location)).text
                assert (
                    await browser.post(
                        f"/admin/kgs/{kg}/settings/provider",
                        data=provider_fields | {"idempotency_key": "stale-profile-selection"},
                    )
                ).status_code == 409
                registry.profiles[profile.id] = profile.model_copy(
                    update={"data_policy_approved": False}
                )
                revoked = await browser.get(location)
                assert "Model approval needed" in revoked.text
                assert "Ready for your sources" not in revoked.text
                overview = await browser.get(f"/admin/kgs/{kg}")
                assert overview.status_code == 200
                assert "No sources have been published yet" in overview.text
                assert 'aria-label="KG workspace"' not in overview.text
                assert (await browser.get("/admin/storage")).status_code == 200
                sign_in(reader)
                assert (await browser.get(location)).status_code == 404
                assert (
                    await browser.post(
                        f"/admin/kgs/{kg}/settings/provider",
                        data={
                            "csrf_token": reader.csrf,
                            "profile_id": str(uuid4()),
                            "expected_revision": "0",
                            "idempotency_key": "reader-provider",
                        },
                    )
                ).status_code == 404
                await app.state.identity.sessions.revoke(owner.session_id)
                sign_in(owner)
                assert (await browser.get("/admin/kgs/new")).status_code == 401
