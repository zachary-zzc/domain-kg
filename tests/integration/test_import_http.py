"""Signed import commands use owned uploads and sealed plans, with current policy."""

import httpx
import pytest
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_legacy_import import upload_csv
from test_publication import make_publishing
from test_transport_authorization import settings_for

from domain_kg.app import create_app
from domain_kg.identity.models import Action
from domain_kg.imports.models import ImportSource
from domain_kg.imports.service import Importer
from domain_kg.providers.profiles import ProviderRegistry
from domain_kg.providers.service import ProviderService

pytestmark = pytest.mark.storage


async def test_execute_requires_approved_embedding_before_admitting(material_service, team):
    materials, editor, uploaded = await upload_csv(material_service, team)
    importer = Importer(materials)
    await importer.initialize()
    plan = await importer.plan(
        editor,
        ImportSource(
            kg_id=uploaded.kg_id,
            upload_id=uploaded.upload_id,
            format="csv",
            idempotency_key="unconfigured-plan",
        ),
    )
    from domain_kg.errors import DomainError, ErrorCode

    providers = ProviderService(materials.policy, materials.router, ProviderRegistry())
    with pytest.raises(DomainError) as denied:
        await Importer(materials, providers).execute(editor, plan)
    assert denied.value.code == ErrorCode.PROVIDER_UNAVAILABLE
    assert not await materials.database.jobs.find_one({"kg_id": str(uploaded.kg_id)})
    assert not await materials.database.materials.find_one({"kg_id": str(uploaded.kg_id)})


async def test_signed_http_plan_execute_seal_replay_and_revocation(
    material_service, team, catalog, issuer_fixture
):
    _, editor, queue, coordinator, _, _ = await make_publishing(
        material_service, team, restricted=True
    )
    _, _, uploaded = await upload_csv(material_service, team)
    identities, policy, principals, members, org, _ = team
    kg = uploaded.kg_id
    _, state, transport, sign = issuer_fixture
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
            token = sign({"sub": "editor", "scope": "domain_kg:read domain_kg:write"})
            headers = {
                "Authorization": f"Bearer {token}",
                "x-domain-kg-organization": str(org),
            }
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                headers=headers,
            ) as http:
                path = f"/api/v1/kgs/{kg}/imports"
                command = {
                    "operation": "plan",
                    "source": {
                        "kg_id": str(kg),
                        "upload_id": str(uploaded.upload_id),
                        "format": "csv",
                        "idempotency_key": "http-import",
                    },
                }
                denied = await http.post(
                    path,
                    json=command,
                    headers={"Authorization": f"Bearer {sign()}"},
                )
                assert denied.status_code == 403
                preview = await http.post(path, json=command)
                assert preview.status_code == 200, preview.text
                plan = preview.json()
                assert plan["support_status"] == "imported_unverified"
                assert all(
                    secret not in preview.text for secret in ("source A", "object_id", "dsn")
                )
                forged = await http.post(
                    path,
                    json={"operation": "execute", "plan": plan | {"assertions": 999}},
                )
                assert forged.status_code == 422
                assert not await queue.database.jobs.find_one({"kind": "import"})
                unknown = await http.post(path, json=command | {"principal": "owner"})
                assert unknown.status_code == 422 and "source A" not in unknown.text
                accepted = await http.post(path, json={"operation": "execute", "plan": plan})
                assert accepted.status_code == 202, accepted.text
                replay = await http.post(path, json={"operation": "execute", "plan": plan})
                assert replay.status_code == 202 and replay.json() == accepted.json()
                assert await queue.database.jobs.count_documents({"kind": "import"}) == 1
                owner = await policy.resolve_access(principals["owner"], [kg], Action.MANAGE_GRANTS)
                await policy.set_grant(
                    owner,
                    kg,
                    "member",
                    members["editor"].id,
                    None,
                    idempotency_key="revoke-import-editor",
                )
                revoked = await http.post(path, json={"operation": "execute", "plan": plan})
                assert revoked.status_code == 404
