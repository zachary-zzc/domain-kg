"""A source node fence also hides its historical human fields before paging."""
# ruff: noqa: F811

import pytest
from test_curation import alpha_id, publish_curation
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.identity.models import Action
from domain_kg.revisions.models import CurationCommand, HistoryRequest, NodeChanges, NodeUpdate
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


async def test_suppressed_scope_history_cannot_leak_through_an_independent_visible_source(
    publishing, team
):
    _, editor, queue, coordinator, _, _ = publishing
    sources = []
    for key in ("history-mask-a", "history-mask-b"):
        source, job, checkpoint = await prepare(publishing, key=key)
        await coordinator.publish(editor, await ready(publishing, job, checkpoint))
        sources.append(source)
    alpha = await alpha_id(queue, editor, job.kg_id)
    service = RevisionService(queue, coordinator.providers)
    for source, name in ((sources[0], "Hidden A correction"), (sources[1], "Visible B correction")):
        receipt = await service.update_node(
            editor,
            NodeUpdate(
                kg_id=job.kg_id,
                entity_id=alpha,
                expected_revision=0,
                source_ids=(source.material_id,),
                changes=NodeChanges(name=name),
                reason=name,
                idempotency_key="review-" + str(source.material_id),
            ),
        )
        await publish_curation(queue, coordinator, editor, receipt)
    await service.curate_node(
        editor,
        CurationCommand(
            kg_id=job.kg_id,
            entity_id=alpha,
            action="suppress",
            expected_revision=1,
            source_ids=(sources[0].material_id,),
            reason="Fence source A",
            idempotency_key="history-mask-node",
        ),
    )
    reader = await queue.policy.resolve_access(team[2]["reader"], [job.kg_id], Action.READ)
    page = await service.get_revisions(
        reader,
        HistoryRequest(
            kg_id=job.kg_id,
            target_id=alpha,
            limit=1,
        ),
    )
    assert len(page.items) == 1 and page.items[0].after.name == "Visible B correction"
    assert "Hidden A correction" not in page.model_dump_json()
    assert page.next_cursor is None
