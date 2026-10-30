"""Agent credentials retain their limits across HTTP, MCP and account lifecycle changes."""

# ruff: noqa: F811
import json
from uuid import UUID, uuid4

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from test_identity import issuer_fixture as issuer_fixture
from test_identity_bridge import bridge as bridge

from domain_kg.errors import DomainError
from domain_kg.identity.bridge.service import IdentityBridge
from domain_kg.identity.models import Action, Principal

pytestmark = pytest.mark.storage


def bearer(token):
    return {"Authorization": "Bearer " + token}


async def test_readonly_existing_admin_cannot_manage_http_or_mcp(bridge, team):
    async with bridge() as (http, app, boot, exchange, _, _, _, state):
        existing = await boot("owner")
        assert existing.status_code == 200 and existing.json()["created"] is False
        token = await exchange("owner")
        assert token.status_code == 200, token.text
        headers = bearer(token.json()["access_token"])
        assert (await http.get("/api/v1/me", headers=headers)).status_code == 200

        for path in ("members", "groups", "service-identities", "identity-integrations"):
            denied = await http.get("/api/v1/" + path, headers=headers)
            assert denied.status_code == 403, denied.text
        for path, command in (
            (
                "members",
                {
                    "issuer": state["issuer"],
                    "subject": "delegation-escape",
                    "role": "admin",
                    "idempotency_key": "denied-member",
                },
            ),
            ("groups", {"name": "Escaped group", "idempotency_key": "denied-group"}),
        ):
            denied = await http.post("/api/v1/" + path, headers=headers, json=command)
            assert denied.status_code == 403, denied.text

        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://localhost:8000",
            headers=headers,
        ) as wire:
            async with Client(
                streamable_http_client("http://localhost:8000/mcp", http_client=wire)
            ) as client:
                for name, arguments in (
                    ("domain_kg_list_team", {}),
                    (
                        "domain_kg_create_group",
                        {"command": {"name": "MCP escape", "idempotency_key": "mcp-escape"}},
                    ),
                    (
                        "domain_kg_add_material",
                        {
                            "command": {
                                "kg_id": str(state["kg"]),
                                "title": "Read-only write",
                                "inline_text": "The same KG is still read-only for this Agent.",
                                "idempotency_key": "same-kg-denied",
                            }
                        },
                    ),
                ):
                    result = await client.call_tool(name, arguments)
                    assert result.is_error, (name, result)
        assert not await team[0].database.members.find_one({"subject": "delegation-escape"})
        assert not await team[0].database.groups.find_one({"name": "Escaped group"})
        assert not await team[0].database.groups.find_one({"name": "MCP escape"})


@pytest.mark.parametrize("bridge", [{"integration": {"auto_approve": False}}], indirect=True)
async def test_pending_member_cannot_use_legacy_token_before_approval(bridge, team):
    async with bridge() as (http, _, boot, exchange, _, _, integration, state):
        pending = await boot()
        assert pending.status_code == 200, pending.text
        assert pending.json()["status"] == "pending_approval"
        assert pending.json()["connection_allowed"] is False
        legacy = bearer(state["sign"]({"sub": "new-user"}))
        assert (await http.get("/api/v1/me", headers=legacy)).status_code == 403
        assert (await exchange()).status_code == 401
        member = await team[0].database.members.find_one({"id": pending.json()["member_id"]})
        assert member["role"] == "member"

        links = await http.get(f"/api/v1/identity-integrations/{integration}/links")
        assert links.status_code == 200, links.text
        link = links.json()["items"][0]
        approved = await http.post(
            f"/api/v1/identity-links/{link['id']}/approval",
            json={
                "expected_revision": link["revision"],
                "approved": True,
                "idempotency_key": "approve-pending",
            },
        )
        assert approved.status_code == 200, approved.text
        assert (await http.get("/api/v1/me", headers=legacy)).status_code == 200
        issued = await exchange()
        assert issued.status_code == 200, issued.text
        assert (
            await http.get("/api/v1/me", headers=bearer(issued.json()["access_token"]))
        ).status_code == 200


async def test_ordered_events_reject_replay_and_enable_does_not_revive_connections(bridge):
    async with bridge() as (http, _, boot, exchange, _, caller, integration, state):
        assert (await boot()).status_code == 200
        issued = await exchange()
        assert issued.status_code == 200, issued.text
        old_access = bearer(issued.json()["access_token"])
        legacy = bearer(state["sign"]({"sub": "new-user"}))

        async def event(event_id, version, operation, **fields):
            return await http.post(
                "/api/v1/identity-bridge/events",
                headers=bearer(caller),
                json={
                    "integration_id": integration,
                    "event_id": event_id,
                    "external_subject": "new-user",
                    "source_version": version,
                    "operation": operation,
                    **fields,
                },
            )

        first = await event("profile-v2", 2, "profile", display_name="Current name")
        assert first.status_code == 200 and first.json()["applied"] is True
        repeated = await event("profile-v2", 2, "profile", display_name="Current name")
        assert repeated.status_code == 200 and repeated.json() == first.json()
        changed = await event("profile-v2", 2, "profile", display_name="Different input")
        assert changed.status_code == 409 and changed.json()["code"] == "IDEMPOTENCY_CONFLICT"
        stale = await event("stale-disable", 1, "disable")
        assert stale.status_code == 200 and stale.json()["applied"] is False
        assert stale.json()["source_version"] == 2
        assert (await http.get("/api/v1/me", headers=old_access)).status_code == 200

        disabled = await event("disable-v3", 3, "disable")
        assert disabled.status_code == 200 and disabled.json()["applied"] is True
        assert (await http.get("/api/v1/me", headers=old_access)).status_code == 401
        assert (await http.get("/api/v1/me", headers=legacy)).status_code == 403
        stale = await event("stale-enable", 2, "enable")
        assert stale.status_code == 200 and stale.json()["applied"] is False
        assert (await exchange()).status_code == 401

        enabled = await event("enable-v4", 4, "enable")
        assert enabled.status_code == 200 and enabled.json()["applied"] is True
        assert (await http.get("/api/v1/me", headers=legacy)).status_code == 200
        assert (await http.get("/api/v1/me", headers=old_access)).status_code == 401
        fresh = await exchange()
        assert fresh.status_code == 200, fresh.text
        assert (
            await http.get("/api/v1/me", headers=bearer(fresh.json()["access_token"]))
        ).status_code == 200


async def test_integration_disable_and_unbound_exchange_token_never_fall_back(bridge):
    async with bridge() as (http, _, boot, exchange, _, _, integration, state):
        assert (await boot()).status_code == 200
        issued = await exchange()
        assert issued.status_code == 200, issued.text
        access = bearer(issued.json()["access_token"])
        unbound = bearer(
            state["sign"](
                {
                    "sub": "new-user",
                    "azp": "kg-exchanger",
                    "jti": str(uuid4()),
                    "scope": "domain_kg:read domain_kg:write domain_kg:admin",
                }
            )
        )
        assert (await http.get("/api/v1/me", headers=unbound)).status_code == 401
        assert (await http.get("/api/v1/me", headers=access)).status_code == 200

        disabled = await http.patch(
            f"/api/v1/identity-integrations/{integration}",
            json={"active": False, "expected_revision": 0, "idempotency_key": "disable"},
        )
        assert disabled.status_code == 200, disabled.text
        assert (await http.get("/api/v1/me", headers=access)).status_code == 401
        assert (await boot()).status_code == 401
        assert (await exchange()).status_code == 401
        enabled = await http.patch(
            f"/api/v1/identity-integrations/{integration}",
            json={"active": True, "expected_revision": 1, "idempotency_key": "enable"},
        )
        assert enabled.status_code == 200, enabled.text
        assert (await http.get("/api/v1/me", headers=access)).status_code == 401
        assert (await http.get("/api/v1/me", headers=unbound)).status_code == 401
        assert (await exchange()).status_code == 200


@pytest.mark.parametrize(
    "bridge", [{"profile": {"tenant_claim": "tid", "tenant_id": "tenant-a"}}], indirect=True
)
async def test_wrong_tenant_caller_scope_and_foreign_kg_are_rejected(bridge, team):
    async with bridge() as (http, _, boot, _, assertion, caller, integration, state):
        for claims in (
            {},
            {"tid": "tenant-b"},
            {"iss": "https://other.example", "tid": "tenant-a"},
        ):
            denied = await boot(token=assertion(**claims))
            assert denied.status_code == 401, denied.text
        assert state["broker_calls"] == 0
        valid_assertion = assertion(tid="tenant-a")
        invalid_caller = state["sign"](
            {
                "sub": "platform-service",
                "azp": "platform-server",
                "aud": "https://kg.example/identity-bridge",
                "scope": "domain_kg:read",
            }
        )
        denied = await http.post(
            "/api/v1/identity-bridge/bootstrap",
            headers=bearer(invalid_caller) | {"Idempotency-Key": "wrong-caller-scope"},
            json={
                "integration_id": integration,
                "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
                "subject_token": valid_assertion,
            },
        )
        assert denied.status_code == 401
        assert (await boot(token=valid_assertion)).status_code == 200

        async def requested(kgs, actions):
            return await http.post(
                "/api/v1/identity-bridge/token",
                headers=bearer(caller) | {"Idempotency-Key": str(uuid4())},
                json={
                    "integration_id": integration,
                    "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
                    "subject_token": assertion(tid="tenant-a"),
                    "kg_ids": kgs,
                    "actions": actions,
                },
            )

        assert (await requested([str(team[5])], ["read"])).status_code == 403
        assert (await requested([str(state["kg"])], ["backup"])).status_code == 403
        assert (await requested([str(state["kg"])], ["manage_organization"])).status_code == 422
        issued = await requested([str(state["kg"])], ["read"])
        assert issued.status_code == 200, issued.text
        denied = await http.get(
            "/api/v1/me",
            headers=bearer(issued.json()["access_token"])
            | {"x-domain-kg-organization": str(uuid4())},
        )
        assert denied.status_code == 403, denied.text


async def test_verified_identity_mapping_cannot_be_rebound(bridge, team):
    async with bridge() as (_, _, boot, exchange, _, _, integration, state):
        first = await boot()
        assert first.status_code == 200, first.text
        identifier = first.json()["member_id"]
        state["canonical_subject"] = "owner"
        changed = await boot()
        assert changed.status_code in (401, 409), changed.text
        assert (await exchange()).status_code == 401
        link = await team[0].database.identity_links.find_one(
            {
                "integration_id": integration,
                "external_subject": "new-user",
            }
        )
        assert link["member_id"] == identifier and link["canonical_subject"] == "new-user"
        state.pop("canonical_subject")
        repeated = await boot()
        assert repeated.status_code == 200 and repeated.json()["member_id"] == identifier
        assert repeated.json()["created"] is False


async def test_changed_trust_profile_disables_existing_connections(bridge, team):
    async with bridge() as (http, app, boot, exchange, _, _, integration, state):
        assert (await boot()).status_code == 200
        issued = await exchange()
        assert issued.status_code == 200, issued.text
        path = state["profile_path"]
        original_configuration = path.read_text()
        configuration = json.loads(path.read_text())
        configuration["profiles"][0]["allowed_org_ids"] = [str(uuid4())]
        path.write_text(json.dumps(configuration))
        identity = app.state.identity
        restarted = IdentityBridge(identity.settings, team[0], identity.verifier, identity.client)
        await restarted.initialize()
        integration_record = await team[0].database.identity_integrations.find_one(
            {"id": integration}
        )
        assert integration_record["active"] is False
        assert (
            await http.get("/api/v1/me", headers=bearer(issued.json()["access_token"]))
        ).status_code == 401
        assert (await boot()).status_code == 401
        path.write_text(original_configuration)
        restored = IdentityBridge(identity.settings, team[0], identity.verifier, identity.client)
        await restored.initialize()
        enabled = await http.patch(
            f"/api/v1/identity-integrations/{integration}",
            json={
                "active": True,
                "expected_revision": integration_record["revision"],
                "idempotency_key": "restore-approved-profile",
            },
        )
        assert enabled.status_code == 200, enabled.text
        assert (
            await http.get("/api/v1/me", headers=bearer(issued.json()["access_token"]))
        ).status_code == 401
        assert (await exchange()).status_code == 200


async def test_upstream_disable_blocks_last_admin_and_cached_actor_until_recovery(bridge, team):
    async with bridge() as (http, _, boot, exchange, _, caller, integration, _):
        assert (await boot("owner")).status_code == 200
        issued = await exchange("owner")
        assert issued.status_code == 200, issued.text
        cached_actor = team[3]["owner"]
        event = {
            "integration_id": integration,
            "event_id": "disable-last-admin",
            "external_subject": "owner",
            "source_version": 1,
            "operation": "disable",
        }
        disabled = await http.post(
            "/api/v1/identity-bridge/events", headers=bearer(caller), json=event
        )
        assert disabled.status_code == 200, disabled.text
        assert disabled.json()["recovery_required"] is True
        assert (await http.get("/api/v1/me")).status_code == 403
        assert (
            await http.get("/api/v1/me", headers=bearer(issued.json()["access_token"]))
        ).status_code == 401
        with pytest.raises(DomainError) as blocked:
            await team[0].create_group(
                cached_actor, "Cached actor bypass", idempotency_key="cached-actor"
            )
        assert blocked.value.code == "FORBIDDEN"
        assert not await team[0].database.groups.find_one({"name": "Cached actor bypass"})
        organization = await team[0].database.organizations.find_one({"_id": str(team[4])})
        assert organization["active_admins"] == 1

        restored = await http.post(
            "/api/v1/identity-bridge/events",
            headers=bearer(caller),
            json=event
            | {"event_id": "restore-last-admin", "source_version": 2, "operation": "enable"},
        )
        assert restored.status_code == 200, restored.text
        assert (await http.get("/api/v1/me")).status_code == 200
        assert (
            await http.get("/api/v1/me", headers=bearer(issued.json()["access_token"]))
        ).status_code == 401


@pytest.mark.parametrize("bridge", [{"extra_profiles": 1}], indirect=True)
async def test_synced_groups_preserve_manual_and_other_integration_ownership(
    bridge, team, material_service
):
    identities, policy, principals, members, org, outside_kg = team
    kg = material_service[1]
    mapped = await identities.create_group(
        members["owner"], "Approved upstream team", idempotency_key="mapped-group"
    )
    outside = await identities.create_group(
        members["owner"], "Unmapped foreign KG", idempotency_key="outside-group"
    )
    for graph, group in ((kg, mapped), (outside_kg, outside)):
        access = await policy.resolve_access(principals["owner"], [graph], Action.MANAGE_GRANTS)
        await policy.set_grant(
            access, graph, "group", group.id, "reader", idempotency_key=f"group-grant-{group.id}"
        )
    mappings = [{"external_group": "approved-team", "group_id": str(mapped.id)}]
    async with bridge(integration_options={"default_grants": [], "group_mappings": mappings}) as (
        http,
        _,
        boot,
        exchange,
        assertion,
        caller,
        integration,
        state,
    ):
        crossed = await http.post(
            "/api/v1/identity-integrations",
            json={
                "profile_id": state["profile"]["id"],
                "name": "Cross KG group attempt",
                "allowed_kg_ids": [str(kg)],
                "default_grants": [],
                "auto_approve": True,
                "group_mappings": [{"external_group": "outside", "group_id": str(outside.id)}],
                "idempotency_key": "cross-kg-mapping",
            },
        )
        assert crossed.status_code == 404, crossed.text
        bootstrapped = await boot()
        assert bootstrapped.status_code == 200, bootstrapped.text
        member_id = UUID(bootstrapped.json()["member_id"])
        user = Principal(issuer=state["issuer"], subject="new-user", scopes={"domain_kg:read"})
        with pytest.raises(DomainError):
            await policy.resolve_access(user, [kg], Action.READ, org_id=org)

        async def sync(identifier, credential, version, groups):
            result = await http.post(
                "/api/v1/identity-bridge/events",
                headers=bearer(credential),
                json={
                    "integration_id": identifier,
                    "event_id": f"groups-{version}",
                    "external_subject": "new-user",
                    "source_version": version,
                    "operation": "profile",
                    "groups": groups,
                },
            )
            assert result.status_code == 200, result.text

        await sync(integration, caller, 1, ["approved-team", "outside", str(outside.id)])
        current = await policy.resolve_access(user, [kg], Action.READ, org_id=org)
        assert current.groups == {mapped.id}
        assert current.grants[0].role == "reader"
        with pytest.raises(DomainError):
            await policy.resolve_access(user, [outside_kg], Action.READ, org_id=org)
        assert (await exchange()).status_code == 200
        raw_group = await identities.database.groups.find_one({"id": str(mapped.id)})
        assert raw_group["member_ids"] == []

        second_profile = state["profiles"][1]
        second = await http.post(
            "/api/v1/identity-integrations",
            json={
                "profile_id": second_profile["id"],
                "name": "Independent platform",
                "allowed_kg_ids": [str(kg)],
                "allowed_actions": ["read", "search"],
                "default_grants": [],
                "group_mappings": mappings,
                "auto_approve": True,
                "idempotency_key": "independent-platform",
            },
        )
        assert second.status_code == 201, second.text
        second_id = second.json()["id"]
        second_caller = state["sign"](
            {
                "sub": second_profile["caller_subject"],
                "azp": second_profile["caller_client_id"],
                "aud": second_profile["caller_audience"],
                "scope": "identity:provision identity:sync",
            }
        )
        linked = await http.post(
            "/api/v1/identity-bridge/bootstrap",
            headers=bearer(second_caller) | {"Idempotency-Key": "second-link"},
            json={
                "integration_id": second_id,
                "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
                "subject_token": assertion(),
            },
        )
        assert linked.status_code == 200, linked.text
        assert linked.json()["member_id"] == str(member_id)
        supports = {"org_id": str(org), "member_id": str(member_id)}
        await sync(second_id, second_caller, 1, ["approved-team"])
        assert await identities.database.identity_group_supports.count_documents(supports) == 2
        await sync(second_id, second_caller, 2, [])
        assert await identities.database.identity_group_supports.count_documents(supports) == 1
        remaining = await identities.database.identity_group_supports.find_one(supports)
        assert remaining["integration_id"] == integration
        assert (await policy.resolve_access(user, [kg], Action.READ, org_id=org)).groups == {
            mapped.id
        }
        second_links = await http.get(f"/api/v1/identity-integrations/{second_id}/links")
        assert second_links.status_code == 200, second_links.text
        second_link = second_links.json()["items"][0]
        withdrawn = await http.post(
            f"/api/v1/identity-links/{second_link['id']}/approval",
            json={
                "approved": False,
                "expected_revision": second_link["revision"],
                "idempotency_key": "withdraw-second-link",
            },
        )
        assert withdrawn.status_code == 200, withdrawn.text
        assert (await policy.resolve_access(user, [kg], Action.READ, org_id=org)).groups == {
            mapped.id
        }
        assert (await exchange()).status_code == 200

        await identities.set_group_member(
            members["owner"], mapped.id, member_id, present=True, idempotency_key="manual-support"
        )
        await sync(integration, caller, 2, [])
        assert await identities.database.identity_group_supports.count_documents(supports) == 0
        raw_group = await identities.database.groups.find_one({"id": str(mapped.id)})
        assert raw_group["member_ids"] == [str(member_id)]
        assert (await policy.resolve_access(user, [kg], Action.READ, org_id=org)).groups == {
            mapped.id
        }


async def test_pending_link_group_support_never_authorizes_before_approval(
    bridge, team, material_service
):
    identities, policy, principals, members, org, _ = team
    kg = material_service[1]
    group = await identities.create_group(
        members["owner"], "Pending upstream group", idempotency_key="pending-group"
    )
    owner_access = await policy.resolve_access(principals["owner"], [kg], Action.MANAGE_GRANTS)
    await policy.set_grant(
        owner_access, kg, "group", group.id, "reader", idempotency_key="pending-group-grant"
    )
    async with bridge(
        integration_options={
            "auto_approve": False,
            "default_grants": [],
            "group_mappings": [{"external_group": "pending-team", "group_id": str(group.id)}],
        }
    ) as (http, _, boot, exchange, _, caller, integration, state):
        pending = await boot()
        assert pending.status_code == 200 and pending.json()["status"] == "pending_approval"
        synchronized = await http.post(
            "/api/v1/identity-bridge/events",
            headers=bearer(caller),
            json={
                "integration_id": integration,
                "event_id": "pending-groups-v1",
                "external_subject": "new-user",
                "source_version": 1,
                "operation": "profile",
                "groups": ["pending-team"],
            },
        )
        assert synchronized.status_code == 200, synchronized.text
        assert (
            await identities.database.identity_group_supports.count_documents(
                {
                    "org_id": str(org),
                    "member_id": pending.json()["member_id"],
                }
            )
            == 1
        )
        user = Principal(issuer=state["issuer"], subject="new-user", scopes={"domain_kg:read"})
        with pytest.raises(DomainError):
            await policy.resolve_access(user, [kg], Action.READ, org_id=org)
        assert (await exchange()).status_code == 401
        assert (
            await http.get("/api/v1/me", headers=bearer(state["sign"]({"sub": "new-user"})))
        ).status_code == 403
        link = await identities.database.identity_links.find_one({"integration_id": integration})
        approved = await http.post(
            f"/api/v1/identity-links/{link['id']}/approval",
            json={
                "approved": True,
                "expected_revision": link["revision"],
                "idempotency_key": "approve-supported-pending",
            },
        )
        assert approved.status_code == 200, approved.text
        assert (await policy.resolve_access(user, [kg], Action.READ, org_id=org)).groups == {
            group.id
        }
        assert (await exchange()).status_code == 200
