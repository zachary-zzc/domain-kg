"""Owner material grant commands enforce revisions, ownership and safe projections."""

from uuid import uuid4

import pytest
from test_entity_resolution import TEXT

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.material_grants import MaterialACLChange, MaterialGrantManagement
from domain_kg.identity.models import Action
from domain_kg.materials.models import AddMaterial

pytestmark = pytest.mark.storage


async def test_owner_material_acl_is_revisioned_replayable_and_namespace_safe(
    material_service, team
):
    materials, kg = material_service
    _, policy, principals, members, _, _ = team
    editor = await policy.resolve_access(principals["editor"], [kg], Action.INGEST)
    source = await materials.add(
        editor,
        AddMaterial(kg_id=kg, title="ACL fixture", inline_text=TEXT, idempotency_key="acl-source"),
    )
    owner = await policy.resolve_access(principals["owner"], [kg], Action.MANAGE_GRANTS)
    service = MaterialGrantManagement(policy)
    command = MaterialACLChange(
        kg_id=kg,
        material_id=source.material_id,
        acl_mode="restricted",
        member_ids=(members["editor"].id,),
        expected_revision=owner.policy_revision,
        idempotency_key="material-acl",
    )
    with pytest.raises(DomainError):
        await service.get(editor, kg, source.material_id)
    with pytest.raises(DomainError):
        await service.set(editor, command)
    initial = await service.get(owner, kg, source.material_id)
    assert initial.acl_mode == "kg" and initial.member_ids == (members["editor"].id,)
    result = await service.set(owner, command)
    owner = await policy.resolve_access(principals["owner"], [kg], Action.MANAGE_GRANTS)
    assert await service.set(owner, command) == result
    assert (await service.get(owner, kg, source.material_id)).member_ids == (members["editor"].id,)
    reader = await policy.resolve_access(principals["reader"], [kg], Action.READ)
    assert not await policy.material_visible(reader, kg, source.material_id)
    with pytest.raises(DomainError) as stale:
        await service.set(owner, command.model_copy(update={"idempotency_key": "stale"}))
    assert stale.value.code == ErrorCode.VERSION_CONFLICT
    with pytest.raises(DomainError) as foreign:
        await service.set(
            owner,
            command.model_copy(
                update={
                    "member_ids": (uuid4(),),
                    "expected_revision": owner.policy_revision,
                    "idempotency_key": "foreign",
                }
            ),
        )
    assert foreign.value.code == ErrorCode.RESOURCE_NOT_ACCESSIBLE
