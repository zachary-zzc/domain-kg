"""Rebuilt generation history survives verified backup/restore and a new owner's rollback."""
# ruff: noqa: F811

import pytest
from test_curation import alpha_id, publish_curation
from test_publication import publishing  # noqa: F401
from test_restore_preparation import setup_restore

from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.operations.generation import GenerationService
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import GenerationCommand
from domain_kg.operations.restore import RestoreService
from domain_kg.revisions.models import NodeChanges, NodeUpdate, RollbackRevision
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


async def test_rebuilt_generation_backup_restore_preserves_history_and_future_rebuild(
    publishing, team, catalog, monkeypatch
):
    _, editor, queue, coordinator, _, _ = publishing
    curation_ids = []

    async def repair_before_backup(source):
        alpha = await alpha_id(queue, editor, source.kg_id)
        receipt = await RevisionService(queue, coordinator.providers).update_node(
            editor,
            NodeUpdate(
                kg_id=source.kg_id,
                entity_id=alpha,
                expected_revision=0,
                source_ids=(source.material_id,),
                changes=NodeChanges(name="Reviewed Alpha"),
                reason="Original reviewed intent",
                idempotency_key="repair-backup-curation",
            ),
        )
        await publish_curation(queue, coordinator, editor, receipt)
        curation_ids.append(receipt.revision_id)
        owner = await queue.policy.resolve_access(
            team[2]["owner"], [source.kg_id], Action.CONFIGURE
        )
        await OwnerOperations(queue).submit(
            owner,
            GenerationCommand(
                kg_id=source.kg_id,
                expected_published_revision=2,
                idempotency_key="repair-before-backup",
            ),
        )

        async def forbidden(*args, **kwargs):
            pytest.fail("repair and unchanged target text must not call an embedding model")

        monkeypatch.setattr(coordinator.providers, "embeddings", forbidden)
        await GenerationService(queue).run(
            await queue.claim("repair-backup", kinds=("generation",))
        )

    source, _, job, target, _ = await setup_restore(
        publishing, team, catalog, before_backup=repair_before_backup
    )
    await RestoreService(queue, coordinator.providers).run(job)
    owner = await queue.policy.resolve_access(team[2]["owner"], [target.target.kg_id], Action.READ)
    alpha = await alpha_id(queue, owner, target.target.kg_id)
    node = await GraphService(queue).get_node(owner, target.target.kg_id, alpha)
    assert (
        node.name == "Reviewed Alpha"
        and node.curations[0].provenance.reason == "Original reviewed intent"
    )
    rollback = await RevisionService(queue, coordinator.providers).rollback(
        owner,
        RollbackRevision(
            kg_id=target.target.kg_id,
            revision_id=curation_ids[0],
            expected_current_revision=1,
            reason="New owner reverses historical edit",
            idempotency_key="rollback-repaired-restored",
        ),
    )
    await publish_curation(queue, coordinator, owner, rollback)
    assert (await GraphService(queue).get_node(owner, target.target.kg_id, alpha)).name == "Alpha"

    current_owner = await queue.policy.resolve_access(
        team[2]["owner"], [target.target.kg_id], Action.CONFIGURE
    )
    await OwnerOperations(queue).submit(
        current_owner,
        GenerationCommand(
            kg_id=target.target.kg_id,
            expected_published_revision=2,
            idempotency_key="rebuild-restored-kg",
        ),
    )
    rebuild = await queue.claim("rebuild-restored", kinds=("generation",))
    await GenerationService(queue).run(rebuild)
    assert await queue.accepted(rebuild)
    assert (
        await GraphService(queue).get_node(current_owner, target.target.kg_id, alpha)
    ).name == "Alpha"
