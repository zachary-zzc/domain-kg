"""An accepted human rename restores as history, without rerunning intent or old grants."""
# ruff: noqa: F811

import pytest
from test_curation import alpha_id, publish_curation
from test_publication import publishing  # noqa: F401
from test_restore_preparation import setup_restore

from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.operations.restore_activation import RestoreActivation
from domain_kg.operations.restore_authority import RestoreAuthority
from domain_kg.operations.restore_control import RestoreControl
from domain_kg.operations.restore_preparation import RestorePreparation
from domain_kg.operations.restore_projections import RestoreProjections
from domain_kg.revisions.models import NodeChanges, NodeUpdate, RollbackRevision
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


async def test_human_rename_history_restores_with_original_author_and_new_scoped_target(
    publishing, team, catalog
):
    from domain_kg.operations.restore_human_history import RestoreHumanHistory

    _, editor, queue, coordinator, _, _ = publishing
    reason = "Human-reviewed name must survive a KG restore."

    async def curate(source):
        alpha = await alpha_id(queue, editor, source.kg_id)
        receipt = await RevisionService(queue, coordinator.providers).update_node(
            editor,
            NodeUpdate(
                kg_id=source.kg_id,
                entity_id=alpha,
                expected_revision=0,
                source_ids=(source.material_id,),
                changes=NodeChanges(
                    name="Reviewed Alpha", description="Historical human description"
                ),
                reason=reason,
                idempotency_key="restore-history-rename",
            ),
        )
        await publish_curation(queue, coordinator, editor, receipt)

    source, _, job, target, _ = await setup_restore(publishing, team, catalog, before_backup=curate)
    staged = await RestorePreparation(queue, coordinator.providers).prepare(job)
    try:
        await RestoreAuthority(queue, coordinator.providers).stage(job, staged)
        await RestoreControl(queue, coordinator.providers).stage(job, staged)
        await RestoreHumanHistory(queue, coordinator.providers).stage(job, staged)
        parts = await RestoreProjections(queue, coordinator.providers).stage(job, staged)
        await RestoreActivation(queue, coordinator.providers).activate(job, staged, parts)
        access = await queue.policy.resolve_access(
            team[2]["owner"], [target.target.kg_id], Action.READ
        )
        alpha = next(
            row.observation.entity.id
            for row in staged.sources[0].document.rows
            if row.observation and row.observation.name == "Alpha"
        )
        node = await GraphService(queue).get_node(access, target.target.kg_id, alpha)
        assert node.name == "Reviewed Alpha" and node.curations
        assert node.curations[0].provenance.reason == reason
        assert node.curations[0].provenance.actor == staged.curations[0].directive.provenance.actor
        assert {observation.name for observation in node.observations} == {"Alpha"}
        history = await queue.database.curation_revisions.find_one(
            {
                "org_id": str(access.org_id),
                "kg_id": str(target.target.kg_id),
                "id": str(staged.curations[0].curation.id),
            }
        )
        assert history["provenance"]["reason"] == reason and history["state"] == "accepted"
        from domain_kg.ingestion.pipeline import configured_pipeline
        from domain_kg.jobs.runner import JobRunner

        editor_target = await queue.policy.resolve_access(
            team[2]["owner"], [target.target.kg_id], Action.CURATE
        )
        receipt = await RevisionService(queue, coordinator.providers).update_node(
            editor_target,
            NodeUpdate(
                kg_id=target.target.kg_id,
                entity_id=alpha,
                expected_revision=1,
                source_ids=(source.material_id,),
                changes=NodeChanges(tags=("post-restore",)),
                reason="Edit after restore",
                idempotency_key="edit-restored-human-history",
            ),
        )
        next_job = await queue.claim("post-restore-curation", kinds=("curate",))
        assert next_job.id == receipt.job_id
        await JobRunner(queue, configured_pipeline(queue, coordinator.providers))._steps(next_job)
        await queue.finish(next_job)
        assert (await queue.get(editor_target, receipt.job_id)).stage == "ready"
        from domain_kg.revisions.journal import PublicationJournal

        view = await PublicationJournal(queue).load_metadata(editor_target, target.target.kg_id)
        assert len(view.curations) == 1, "a later edit must replace its scoped restored binding"
        assert view.curations[0].id == receipt.revision_id
        assert (
            await GraphService(queue).get_node(editor_target, target.target.kg_id, alpha)
        ).name == "Reviewed Alpha"
        rollback = await RevisionService(queue, coordinator.providers).rollback(
            editor_target,
            RollbackRevision(
                kg_id=target.target.kg_id,
                revision_id=staged.curations[0].curation.id,
                expected_current_revision=2,
                reason="Withdraw the restored rename",
                idempotency_key="rollback-restored-name",
            ),
        )
        await publish_curation(queue, coordinator, editor_target, rollback)
        reverted = await GraphService(queue).get_node(editor_target, target.target.kg_id, alpha)
        assert reverted.name == "Alpha"
        assert (
            len(
                (
                    await PublicationJournal(queue).load_metadata(
                        editor_target, target.target.kg_id
                    )
                ).curations
            )
            == 1
        )
    finally:
        await staged.binding.stores.vectors.client.delete_collection(
            staged.binding.stores.vectors.collection
        )
        await staged.binding.stores.graph.driver.execute_query(
            "MATCH (n {org_id:$org,kg_id:$kg}) DETACH DELETE n",
            org=str(target.target.org_id),
            kg=str(target.target.kg_id),
        )
