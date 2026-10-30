"""Local identity mutations preserve effective administrators and graph owners."""

# ruff: noqa: F811
from uuid import UUID

import pytest
from test_identity import issuer_fixture as issuer_fixture
from test_identity_bridge import bridge as bridge

from domain_kg.errors import DomainError
from domain_kg.identity.models import Action, Principal

pytestmark = pytest.mark.storage


async def test_disabled_admin_cannot_justify_demoting_last_effective_admin(bridge, team):
    identities, _, _, members, org, _ = team
    second = await identities.change_member(
        members["owner"],
        members["editor"].id,
        expected_revision=0,
        role="admin",
        idempotency_key="second-local-admin",
    )
    async with bridge() as (http, _, boot, _, _, caller, integration, _):
        assert (await boot("editor")).status_code == 200
        event = {
            "integration_id": integration,
            "event_id": "disable-second-admin",
            "external_subject": "editor",
            "source_version": 1,
            "operation": "disable",
        }
        disabled = await http.post(
            "/api/v1/identity-bridge/events",
            headers={"Authorization": "Bearer " + caller},
            json=event,
        )
        assert disabled.status_code == 200, disabled.text
        with pytest.raises(DomainError) as blocked:
            await identities.change_member(
                members["owner"],
                members["owner"].id,
                expected_revision=0,
                role="member",
                idempotency_key="would-remove-last-usable-admin",
            )
        assert blocked.value.code == "FORBIDDEN"
        persisted = await identities.database.members.find_one({"id": str(members["owner"].id)})
        assert persisted["role"] == "admin" and persisted["revision"] == 0
        organization = await identities.database.organizations.find_one({"_id": str(org)})
        assert organization["active_admins"] == 2

        enabled = await http.post(
            "/api/v1/identity-bridge/events",
            headers={"Authorization": "Bearer " + caller},
            json=event
            | {"event_id": "restore-second-admin", "source_version": 2, "operation": "enable"},
        )
        assert enabled.status_code == 200, enabled.text
        demoted = await identities.change_member(
            members["owner"],
            members["owner"].id,
            expected_revision=0,
            role="member",
            idempotency_key="other-admin-effective-again",
        )
        assert demoted.role == "member"
        assert (
            await identities.require_member(
                Principal(issuer=second.issuer, subject=second.subject), org
            )
        ).role == "admin"


async def test_owner_invariant_counts_only_enabled_members_and_approved_group_supports(
    bridge, team, material_service
):
    identities, policy, principals, members, org, _ = team
    kg = material_service[1]
    group = await identities.create_group(
        members["owner"], "Synced graph owners", idempotency_key="synced-owner-group"
    )
    for kind, identifier in (("group", group.id), ("member", members["editor"].id)):
        current = await policy.resolve_access(principals["owner"], [kg], Action.MANAGE_GRANTS)
        await policy.set_grant(
            current, kg, kind, identifier, "owner", idempotency_key=f"alternate-owner-{kind}"
        )
    async with bridge(
        integration_options={
            "auto_approve": False,
            "default_grants": [],
            "group_mappings": [{"external_group": "owners", "group_id": str(group.id)}],
        }
    ) as (http, _, boot, _, _, caller, integration, state):
        assert (await boot("editor")).status_code == 200

        async def synchronize(subject, operation, groups=None):
            response = await http.post(
                "/api/v1/identity-bridge/events",
                headers={"Authorization": "Bearer " + caller},
                json={
                    "integration_id": integration,
                    "event_id": "event-" + subject,
                    "external_subject": subject,
                    "source_version": 1,
                    "operation": operation,
                    "groups": groups,
                },
            )
            assert response.status_code == 200, response.text

        async def remove_current_owner(key):
            current = await policy.resolve_access(principals["owner"], [kg], Action.MANAGE_GRANTS)
            await policy.set_grant(
                current, kg, "member", members["owner"].id, None, idempotency_key=key
            )

        await synchronize("editor", "disable")
        with pytest.raises(DomainError) as disabled_owner:
            await remove_current_owner("disabled-alternate-owner")
        assert disabled_owner.value.code == "FORBIDDEN"

        pending = await boot()
        assert pending.status_code == 200 and pending.json()["status"] == "pending_approval"
        await synchronize("new-user", "profile", ["owners"])
        with pytest.raises(DomainError) as pending_owner:
            await remove_current_owner("pending-group-owner")
        assert pending_owner.value.code == "FORBIDDEN"
        raw_group = await identities.database.groups.find_one({"id": str(group.id)})
        assert raw_group["member_ids"] == []
        link = await identities.database.identity_links.find_one(
            {
                "integration_id": integration,
                "member_id": pending.json()["member_id"],
            }
        )
        approved = await http.post(
            f"/api/v1/identity-links/{link['id']}/approval",
            json={
                "approved": True,
                "expected_revision": link["revision"],
                "idempotency_key": "approve-effective-owner",
            },
        )
        assert approved.status_code == 200, approved.text
        await remove_current_owner("approved-synced-owner-remains")
        user = Principal(
            issuer=state["issuer"], subject="new-user", scopes={"domain_kg:read", "domain_kg:write"}
        )
        current = await policy.resolve_access(user, [kg], Action.MANAGE_GRANTS, org_id=org)
        assert current.groups == {group.id} and current.grants[0].role == "owner"
        assert (
            await identities.database.identity_group_supports.count_documents(
                {
                    "org_id": str(org),
                    "member_id": str(UUID(pending.json()["member_id"])),
                    "group_id": str(group.id),
                }
            )
            == 1
        )
