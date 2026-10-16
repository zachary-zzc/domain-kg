"""Pending/removed metadata is an explicit editor operation, never evidence retrieval."""

import pytest

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.jobs.queue import JobQueue
from domain_kg.materials.lifecycle import MaterialLifecycle
from domain_kg.materials.management import MaterialManagement
from domain_kg.materials.models import AddMaterial, RemoveMaterial
from domain_kg.models import PageRequest

pytestmark = pytest.mark.storage


async def test_management_metadata_requires_editor_and_current_material_acl(material_service, team):
    materials, kg = material_service
    policy, principals = team[1], team[2]
    editor = await policy.resolve_access(principals["editor"], [kg], Action.INGEST)
    first = await materials.add(
        editor,
        AddMaterial(
            kg_id=kg,
            title="Visible pending note",
            inline_text="DO_NOT_DISCLOSE_BODY",
            idempotency_key="manage-first",
        ),
    )
    hidden = await materials.add(
        editor,
        AddMaterial(
            kg_id=kg,
            title="Private pending title",
            inline_text="Other private body",
            idempotency_key="manage-hidden",
        ),
    )
    await materials.add(
        editor,
        AddMaterial(
            kg_id=kg,
            title="Other visible note",
            inline_text="Independent body",
            idempotency_key="manage-other",
        ),
    )
    owner = await policy.resolve_access(principals["owner"], [kg], Action.MANAGE_GRANTS)
    await policy.set_material_acl(
        owner,
        kg,
        hidden.material_id,
        "restricted",
        member_ids=(),
        group_ids=(),
        idempotency_key="hide-management-material",
    )
    editor = await policy.resolve_access(principals["editor"], [kg], Action.REPLACE)
    queue = JobQueue(policy, materials.router)
    await queue.initialize()
    service = MaterialManagement(queue)
    pending = await service.list(editor, kg, "pending", PageRequest())
    assert len(pending.items) == 2 and "Private pending title" not in pending.model_dump_json()
    assert "DO_NOT_DISCLOSE_BODY" not in pending.model_dump_json()
    before = await service.list(editor, kg, "pending", PageRequest(limit=1))
    assert before.next_cursor
    reader = await policy.resolve_access(principals["reader"], [kg], Action.READ)
    with pytest.raises(DomainError):
        await service.list(reader, kg, "pending", PageRequest())
    removed = await MaterialLifecycle(materials, queue).remove(
        editor,
        RemoveMaterial(
            kg_id=kg,
            material_id=first.material_id,
            expected_revision=0,
            action="exclude",
            idempotency_key="exclude-management-source",
        ),
    )
    withdrawn = await service.list(editor, kg, "removed", PageRequest())
    assert len(withdrawn.items) == 1
    item = withdrawn.items[0]
    assert item.id == first.material_id and item.revision == removed.revision
    assert item.state == "excluded" and item.can_restore and item.title == "Visible pending note"
    with pytest.raises(DomainError) as cursor:
        await service.list(editor, kg, "pending", PageRequest(limit=1, cursor=before.next_cursor))
    assert cursor.value.code == ErrorCode.CURSOR_EXPIRED
