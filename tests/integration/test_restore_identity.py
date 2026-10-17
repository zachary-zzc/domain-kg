"""An accepted merge restores independently of old actors and remains reversible."""
# ruff: noqa: F811

import pytest
from test_curation import alpha_id
from test_identity_curation import finish_identity
from test_publication import publishing  # noqa: F401
from test_restore_preparation import setup_restore

from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.operations.restore_activation import RestoreActivation
from domain_kg.operations.restore_authority import RestoreAuthority
from domain_kg.operations.restore_control import RestoreControl
from domain_kg.operations.restore_human_history import RestoreHumanHistory
from domain_kg.operations.restore_preparation import RestorePreparation
from domain_kg.operations.restore_projections import RestoreProjections
from domain_kg.retrieval.models import SearchRequest
from domain_kg.revisions.models import CurationCommand, RollbackRevision
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


async def test_restored_merge_retains_original_evidence_and_new_owner_can_retract_it(
    publishing, team, catalog
):
    _, editor, queue, coordinator, _, _ = publishing

    async def merge(source):
        graph = GraphService(queue)
        scope = await graph.resolver.resolve(
            editor, SearchRequest(kg_ids=(source.kg_id,), query="Alpha"), source.kg_id
        )
        observations = (await graph.repository.open(scope)).observations
        alpha = await alpha_id(queue, editor, source.kg_id)
        beta = next(row.entity.id for row in observations if row.name == "Beta")
        receipt = await RevisionService(queue, coordinator.providers).curate_node(
            editor,
            CurationCommand(
                kg_id=source.kg_id,
                entity_id=alpha,
                action="merge",
                expected_revision=0,
                member_ids=(alpha, beta),
                member_revisions=((beta, 0),),
                source_ids=(source.material_id,),
                reason="Keep reviewed identity merge",
                idempotency_key="restore-merge",
                dry_run=False,
            ),
        )
        await finish_identity(queue, coordinator, editor, receipt)

    _, _, job, target, _ = await setup_restore(publishing, team, catalog, before_backup=merge)
    staged = await RestorePreparation(queue, coordinator.providers).prepare(job)
    try:
        await RestoreAuthority(queue, coordinator.providers).stage(job, staged)
        await RestoreControl(queue, coordinator.providers).stage(job, staged)
        await RestoreHumanHistory(queue, coordinator.providers).stage(job, staged)
        parts = await RestoreProjections(queue, coordinator.providers).stage(job, staged)
        await RestoreActivation(queue, coordinator.providers).activate(job, staged, parts)
        access = await queue.policy.resolve_access(
            team[2]["owner"], [target.target.kg_id], Action.CURATE
        )
        rows = staged.sources[0].document.rows
        alpha = next(
            row.observation.entity.id
            for row in rows
            if row.observation and row.observation.name == "Alpha"
        )
        beta = next(
            row.observation.entity.id
            for row in rows
            if row.observation and row.observation.name == "Beta"
        )
        node = await GraphService(queue).get_node(access, target.target.kg_id, beta)
        assert node.id == alpha and set(node.identity_members) == {alpha, beta}
        assert {observation.name for observation in node.observations} == {"Alpha", "Beta"}
        receipt = await RevisionService(queue, coordinator.providers).rollback(
            access,
            RollbackRevision(
                kg_id=target.target.kg_id,
                revision_id=staged.curations[0].curation.id,
                expected_current_revision=1,
                reason="New owner reviewed reversal",
                idempotency_key="restored-merge-retraction",
            ),
        )
        await finish_identity(queue, coordinator, access, receipt)
        assert (await GraphService(queue).get_node(access, target.target.kg_id, beta)).id == beta
    finally:
        await staged.binding.stores.vectors.client.delete_collection(
            staged.binding.stores.vectors.collection
        )
        await staged.binding.stores.graph.driver.execute_query(
            "MATCH (n {org_id:$org,kg_id:$kg}) DETACH DELETE n",
            org=str(target.target.org_id),
            kg=str(target.target.kg_id),
        )
