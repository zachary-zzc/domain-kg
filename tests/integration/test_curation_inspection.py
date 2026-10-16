"""The editor counter is exact to the selected, currently accessible source scope."""
# ruff: noqa: F811

import pytest
from test_curation import alpha_id, publish_curation
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.revisions.inspection import CurationInspection
from domain_kg.revisions.models import NodeChanges, NodeUpdate
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


async def test_edit_context_uses_exact_source_heads_and_current_permissions(publishing, team):
    _, editor, queue, coordinator, _, _ = publishing
    sources = []
    for key in ("context-a", "context-b"):
        source, job, checkpoint = await prepare(publishing, key=key)
        await coordinator.publish(editor, await ready(publishing, job, checkpoint))
        sources.append(source.material_id)
    alpha = await alpha_id(queue, editor, job.kg_id)
    service = CurationInspection(queue)
    for source in sources:
        context = await service.edit(editor, job.kg_id, alpha, "node", source_ids=(source,))
        assert context.expected_revision == 0 and context.source_ids == (source,)
    receipt = await RevisionService(queue, coordinator.providers).update_node(
        editor,
        NodeUpdate(
            kg_id=job.kg_id,
            entity_id=alpha,
            source_ids=(sources[0],),
            expected_revision=0,
            changes=NodeChanges(tags=("reviewed",)),
            reason="Pending source-scoped metadata",
            idempotency_key="inspect-pending-scope",
        ),
    )
    pending = await service.edit(editor, job.kg_id, alpha, "node", source_ids=(sources[0],))
    assert pending.expected_revision == 0
    await publish_curation(queue, coordinator, editor, receipt)
    assert (
        await service.edit(editor, job.kg_id, alpha, "node", source_ids=(sources[0],))
    ).expected_revision == 1
    assert (
        await service.edit(editor, job.kg_id, alpha, "node", source_ids=(sources[1],))
    ).expected_revision == 0
    assert (await service.edit(editor, job.kg_id, alpha, "node")).expected_revision == 0
    with pytest.raises(DomainError):
        await service.edit(editor, job.kg_id, alpha, "node", public=True)
    owner = await queue.policy.resolve_access(team[2]["owner"], [job.kg_id], Action.CURATE)
    assert (await service.edit(owner, job.kg_id, alpha, "node", public=True)).source_ids == ()
    reader = await queue.policy.resolve_access(team[2]["reader"], [job.kg_id], Action.READ)
    with pytest.raises(DomainError):
        await service.edit(reader, job.kg_id, alpha, "node")
    await queue.policy.set_material_acl(
        owner,
        job.kg_id,
        sources[0],
        "restricted",
        member_ids=(),
        group_ids=(),
        idempotency_key="hide-edit-context-source",
    )
    fresh = await queue.policy.resolve_access(team[2]["editor"], [job.kg_id], Action.CURATE)
    with pytest.raises(DomainError) as denied:
        await service.edit(fresh, job.kg_id, alpha, "node", source_ids=(sources[0],))
    assert denied.value.code == ErrorCode.RESOURCE_NOT_ACCESSIBLE
