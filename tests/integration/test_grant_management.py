"""Typed owner role commands bind expected revisions and stable replay."""

import pytest

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.grant_models import MemberRoleChange
from domain_kg.identity.grants import GrantManagement
from domain_kg.identity.models import Action
from domain_kg.models import PageRequest

pytestmark = pytest.mark.storage


async def test_grant_management_requires_current_owner_and_expected_policy_revision(team):
    _, policy, principals, members, _, kg = team
    owner = await policy.resolve_access(principals["owner"], [kg], Action.MANAGE_GRANTS)
    reader = await policy.resolve_access(principals["reader"], [kg], Action.READ)
    service = GrantManagement(policy)
    command = MemberRoleChange(
        kg_id=kg,
        subject_kind="member",
        subject_id=members["reader"].id,
        role="editor",
        expected_revision=owner.policy_revision,
        idempotency_key="typed-grant",
    )
    with pytest.raises(DomainError):
        await service.set(reader, command)
    page = await service.list(owner, kg, PageRequest(limit=1))
    assert len(page.items) == 1 and page.next_cursor
    receipt = await service.set(owner, command)
    owner = await policy.resolve_access(principals["owner"], [kg], Action.MANAGE_GRANTS)
    assert receipt.policy_revision == owner.policy_revision
    assert await service.set(owner, command) == receipt
    with pytest.raises(DomainError) as stale:
        await service.set(owner, command.model_copy(update={"idempotency_key": "stale-grant"}))
    assert stale.value.code == ErrorCode.VERSION_CONFLICT
    with pytest.raises(DomainError) as expired:
        await service.list(owner, kg, PageRequest(limit=1, cursor=page.next_cursor))
    assert expired.value.code == ErrorCode.CURSOR_EXPIRED
    with pytest.raises(DomainError):
        await service.set(
            owner,
            command.model_copy(
                update={
                    "subject_id": members["owner"].id,
                    "role": None,
                    "expected_revision": owner.policy_revision,
                    "idempotency_key": "last-owner",
                }
            ),
        )


async def test_grant_cursor_keeps_member_and_group_with_the_same_uuid(team):
    _, policy, principals, members, org, kg = team
    # Member and group UUID namespaces are independent.
    shared = members["reader"].id
    await policy.database.groups.insert_one(
        {
            "_id": str(shared),
            "id": str(shared),
            "org_id": str(org),
            "name": "Shared fixture ID",
            "member_ids": [],
        }
    )
    owner = await policy.resolve_access(principals["owner"], [kg], Action.MANAGE_GRANTS)
    await policy.set_grant(owner, kg, "group", shared, "reader", idempotency_key="group-collision")
    owner = await policy.resolve_access(principals["owner"], [kg], Action.MANAGE_GRANTS)
    service = GrantManagement(policy)
    cursor, seen = None, set()
    for _ in range(5):
        page = await service.list(owner, kg, PageRequest(limit=1, cursor=cursor))
        seen.update((item.subject_kind, item.subject_id) for item in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert ("member", shared) in seen and ("group", shared) in seen
    assert len(seen) == 4
