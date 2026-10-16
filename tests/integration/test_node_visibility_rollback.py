"""Explicit human inspection and owner compensation retain current permissions."""
# ruff: noqa: F811

import pytest
from test_curation import alpha_id, publish_curation
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.revisions.models import (
    CurationCommand,
    HistoryRequest,
    NodeChanges,
    NodeUpdate,
    RollbackRevision,
)
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


async def test_visibility_inspection_requires_curate_and_owner_rollback_preserves_fields(
    publishing, team, monkeypatch
):
    _, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    alpha = await alpha_id(queue, editor, job.kg_id)
    service = RevisionService(queue, coordinator.providers)
    rename = await service.update_node(
        editor,
        NodeUpdate(
            kg_id=job.kg_id,
            entity_id=alpha,
            expected_revision=0,
            source_ids=(source.material_id,),
            changes=NodeChanges(name="Human correction"),
            reason="Reviewed node fields",
            idempotency_key="inspection-human-name",
        ),
    )
    await publish_curation(queue, coordinator, editor, rename)
    fence = await service.curate_node(
        editor,
        CurationCommand(
            kg_id=job.kg_id,
            entity_id=alpha,
            action="suppress",
            expected_revision=1,
            source_ids=(source.material_id,),
            reason="Hold this node",
            idempotency_key="inspection-fence",
        ),
    )
    reader = await queue.policy.resolve_access(team[2]["reader"], [job.kg_id], Action.READ)
    request = HistoryRequest(kg_id=job.kg_id, target_id=alpha, include_suppressed=True)
    with pytest.raises(DomainError):
        await service.get_revisions(reader, request)
    page = await service.get_revisions(editor, request)
    assert [entry.kind for entry in page.items] == ["node", "visibility"]
    action = page.items[1]
    assert action.revision_id == fence.revision_id and action.job_id is None
    assert action.after.suppressed and action.published_revision is None
    with pytest.raises(DomainError):
        await service.rollback(
            editor,
            RollbackRevision(
                kg_id=job.kg_id,
                revision_id=fence.revision_id,
                expected_current_revision=2,
                reason="Not an owner",
                idempotency_key="editor-fence-rollback",
            ),
        )
    owner = await queue.policy.resolve_access(team[2]["owner"], [job.kg_id], Action.CURATE)

    async def forbidden(*args, **kwargs):
        pytest.fail("compensating a fence preserves the accepted manual vector")

    with monkeypatch.context() as patch:
        patch.setattr(coordinator.providers, "extract", forbidden)
        patch.setattr(coordinator.providers, "embeddings", forbidden)
        rollback = RollbackRevision(
            kg_id=job.kg_id,
            revision_id=fence.revision_id,
            expected_current_revision=2,
            reason="Compensate this fence",
            idempotency_key="owner-fence-rollback",
        )
        compensation = await service.rollback(owner, rollback)
        assert compensation.state == "applied" and compensation.target_revision == 3
        assert await service.rollback(owner, rollback) == compensation
    assert (await GraphService(queue).get_node(reader, job.kg_id, alpha)).name == "Human correction"
    page = await service.get_revisions(reader, HistoryRequest(kg_id=job.kg_id, target_id=alpha))
    assert page.items[-1].rollback_of == fence.revision_id
    assert page.items[-1].before.suppressed and not page.items[-1].after.suppressed
