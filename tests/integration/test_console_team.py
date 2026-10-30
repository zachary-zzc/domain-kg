"""Organization admission and owner grant forms keep actor and namespace server-derived."""

import httpx
import pytest
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app
from domain_kg.identity.models import Action, Principal
from domain_kg.materials.models import AddMaterial

pytestmark = pytest.mark.storage


async def test_admin_admission_group_and_owner_grants_apply_through_shared_policy(
    material_service, team, catalog, issuer_fixture
):
    materials, kg = material_service
    identities, policy, principals, _, org, _ = team
    editor = await policy.resolve_access(principals["editor"], [kg], Action.INGEST)
    source = await materials.add(
        editor,
        AddMaterial(
            kg_id=kg,
            title="Group-controlled source",
            inline_text="Tiny group source",
            idempotency_key="team-console-source",
        ),
    )
    _, state, transport, _ = issuer_fixture
    state["issuer"] = principals["owner"].issuer
    async with httpx.AsyncClient(transport=transport) as oidc_client:
        app = create_app(
            settings_for(state["issuer"]),
            identity_repository=identities,
            oidc_http_client=oidc_client,
            catalog_service=catalog[0],
        )
        async with app.router.lifespan_context(app):
            ticket = await app.state.identity.sessions.create(principals["owner"], org)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
                headers={"Origin": "http://localhost:8000"},
            ) as browser:
                assert (await browser.get("/admin/team")).status_code == 200
                admitted = await browser.post(
                    "/admin/team/members",
                    data={
                        "csrf_token": ticket.csrf,
                        "issuer": state["issuer"],
                        "subject": "console-new-human",
                        "role": "member",
                        "idempotency_key": "console-admit-human",
                    },
                )
                assert admitted.status_code == 303
                member = await identities.database.members.find_one(
                    {"org_id": str(org), "subject": "console-new-human"}
                )
                assert member
                created = await browser.post(
                    "/admin/team/groups",
                    data={
                        "csrf_token": ticket.csrf,
                        "name": "Console reviewers",
                        "idempotency_key": "console-create-group",
                    },
                )
                assert created.status_code == 303
                group = await identities.database.groups.find_one(
                    {"org_id": str(org), "name": "Console reviewers"}
                )
                assert group
                joined = await browser.post(
                    f"/admin/team/groups/{group['id']}/members",
                    data={
                        "csrf_token": ticket.csrf,
                        "member_id": member["id"],
                        "operation": "add",
                        "idempotency_key": "console-group-member",
                    },
                )
                assert joined.status_code == 303
                groups = await browser.get("/admin/team?kind=groups")
                assert groups.status_code == 200
                assert 'class="member-chips"' in groups.text
                assert "console-new-human" in groups.text and "Console reviewers" in groups.text
                assert f'<option value="{member["id"]}">' in groups.text
                grants = await browser.get(f"/admin/kgs/{kg}/grants")
                assert grants.status_code == 200
                revision = (await browser.get(f"/api/v1/kgs/{kg}/members")).json()[
                    "policy_revision"
                ]
                granted = await browser.post(
                    f"/admin/kgs/{kg}/grants",
                    data={
                        "csrf_token": ticket.csrf,
                        "subject_kind": "group",
                        "subject_id": group["id"],
                        "role": "reader",
                        "expected_revision": str(revision),
                        "idempotency_key": "console-grant-group",
                    },
                )
                assert granted.status_code == 303
                acl_path = f"/admin/kgs/{kg}/materials/{source.material_id}/acl"
                assert (await browser.get(acl_path)).status_code == 200
                revision = (await browser.get(f"/api/v1/kgs/{kg}/members")).json()[
                    "policy_revision"
                ]
                scoped = await browser.post(
                    acl_path,
                    data={
                        "csrf_token": ticket.csrf,
                        "acl_mode": "restricted",
                        "member_ids": "",
                        "group_ids": group["id"],
                        "expected_revision": str(revision),
                        "idempotency_key": "console-private-group",
                    },
                )
                assert scoped.status_code == 303
                fresh = await app.state.identity.sessions.create(
                    Principal(issuer=state["issuer"], subject="console-new-human"), org
                )
                browser.cookies.clear()
                browser.cookies.set("domain_kg_session", fresh.session_id)
                browser.cookies.set("domain_kg_csrf", fresh.csrf)
                assert (
                    await browser.get(f"/admin/kgs/{kg}/jobs/{source.job_id}")
                ).status_code == 200
                assert (await browser.get("/admin/team")).status_code in (403, 404)
                denied = await browser.post(
                    "/admin/team/members",
                    data={
                        "csrf_token": fresh.csrf,
                        "issuer": state["issuer"],
                        "subject": "forged-admin",
                        "role": "admin",
                        "idempotency_key": "forged-console-admit",
                    },
                )
                assert denied.status_code in (403, 404)
                assert not await identities.database.members.find_one(
                    {"org_id": str(org), "subject": "forged-admin"}
                )


async def test_admin_forms_reject_bad_group_and_manage_service_suspension(
    material_service, team, catalog, issuer_fixture
):
    _, kg = material_service
    identities, _, principals, _, org, _ = team
    _, state, transport, _ = issuer_fixture
    state["issuer"] = principals["owner"].issuer
    async with httpx.AsyncClient(transport=transport) as oidc_client:
        app = create_app(
            settings_for(state["issuer"]),
            identity_repository=identities,
            oidc_http_client=oidc_client,
            catalog_service=catalog[0],
        )
        async with app.router.lifespan_context(app):
            ticket = await app.state.identity.sessions.create(principals["owner"], org)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
                headers={"Origin": "http://localhost:8000"},
            ) as browser:
                invalid = await browser.post(
                    "/admin/team/groups",
                    data={
                        "csrf_token": ticket.csrf,
                        "name": "   ",
                        "idempotency_key": "bad-ui-group",
                    },
                )
                assert invalid.status_code == 422
                created = await browser.post(
                    "/admin/team/services",
                    data={
                        "csrf_token": ticket.csrf,
                        "issuer": state["issuer"],
                        "subject": "console-service",
                        "kg_ids": str(kg),
                        "scopes": "domain_kg:read",
                        "idempotency_key": "console-service-admit",
                    },
                )
                assert created.status_code == 303
                member = await identities.database.members.find_one(
                    {"org_id": str(org), "subject": "console-service"}
                )
                assert member and member["kind"] == "service"
                invalid_revision = await browser.post(
                    f"/admin/team/members/{member['id']}",
                    data={
                        "csrf_token": ticket.csrf,
                        "status": "suspended",
                        "expected_revision": "-1",
                        "idempotency_key": "bad-console-service-revision",
                    },
                )
                assert invalid_revision.status_code == 422
                listing = await browser.get("/admin/team", params={"kind": "services"})
                assert listing.status_code == 200 and "console-service" in listing.text
                changed = await browser.post(
                    f"/admin/team/members/{member['id']}",
                    data={
                        "csrf_token": ticket.csrf,
                        "status": "suspended",
                        "expected_revision": str(member["revision"]),
                        "idempotency_key": "suspend-console-service",
                    },
                )
                assert changed.status_code == 303
                assert (await identities.database.members.find_one({"id": member["id"]}))[
                    "suspended"
                ]
