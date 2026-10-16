"""History exposes authorized human fields and expires cursors at current fences."""
# ruff: noqa: F811

import pytest
from test_curation import alpha_id, publish_curation
from test_entity_resolution import TEXT
from test_publication import make_publishing, prepare, ready

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.materials.lifecycle import MaterialLifecycle
from domain_kg.materials.models import AddMaterial, RemoveMaterial
from domain_kg.revisions.models import HistoryRequest, NodeChanges, NodeUpdate
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


async def test_history_filters_source_ownership_and_expires_cursor_after_removal(
    material_service, team
):
    fixture = await make_publishing(material_service, team, restricted=True)
    materials, editor, queue, coordinator, _, _ = fixture
    sources = []
    for key, acl in (("history-private", "restricted"), ("history-team", "kg")):
        source, job, checkpoint = await prepare(
            fixture,
            command=AddMaterial(
                kg_id=editor.kg_ids[0],
                title=key,
                inline_text=TEXT,
                acl_mode=acl,
                idempotency_key=key,
            ),
        )
        await coordinator.publish(editor, await ready(fixture, job, checkpoint))
        sources.append(source)
    alpha = await alpha_id(queue, editor, job.kg_id)
    service = RevisionService(queue, coordinator.providers)
    for material, revision, changes, key in (
        (sources[0], 0, NodeChanges(name="Private correction"), "history-private-edit"),
        (sources[1], 0, NodeChanges(name="Team correction"), "history-team-edit"),
        (sources[1], 1, NodeChanges(tags=("reviewed",)), "history-team-tags"),
    ):
        receipt = await service.update_node(
            editor,
            NodeUpdate(
                kg_id=job.kg_id,
                entity_id=alpha,
                source_ids=(material.material_id,),
                expected_revision=revision,
                changes=changes,
                reason=key,
                idempotency_key=key,
            ),
        )
        await publish_curation(queue, coordinator, editor, receipt)
    reader = await queue.policy.resolve_access(team[2]["reader"], [job.kg_id], Action.READ)
    request = HistoryRequest(kg_id=job.kg_id, target_id=alpha, kind="node", limit=1)
    first = await service.get_revisions(reader, request)
    assert len(first.items) == 1 and first.next_cursor
    assert first.items[0].after.name == "Team correction"
    assert first.items[0].material_ids == (sources[1].material_id,)
    assert "Private correction" not in first.model_dump_json()
    second = await service.get_revisions(
        reader, request.model_copy(update={"cursor": first.next_cursor})
    )
    assert len(second.items) == 1 and second.items[0].after.tags == ("reviewed",)
    assert second.next_cursor is None
    assert second.items[0].before.name == "Team correction"
    with pytest.raises(DomainError) as other:
        await service.get_revisions(
            editor, request.model_copy(update={"cursor": first.next_cursor})
        )
    assert other.value.code == ErrorCode.CURSOR_EXPIRED
    await MaterialLifecycle(materials, queue).remove(
        editor,
        RemoveMaterial(
            kg_id=job.kg_id,
            material_id=sources[1].material_id,
            action="delete",
            expected_revision=1,
            idempotency_key="history-source-removed",
        ),
    )
    with pytest.raises(DomainError) as expired:
        await service.get_revisions(
            reader, request.model_copy(update={"cursor": first.next_cursor})
        )
    assert expired.value.code == ErrorCode.CURSOR_EXPIRED
    with pytest.raises(DomainError) as hidden:
        await service.get_revisions(reader, request)
    assert hidden.value.code == ErrorCode.RESOURCE_NOT_ACCESSIBLE
