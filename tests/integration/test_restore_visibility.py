"""Restore source-scoped suppression and exercise its later authorized reversal."""
# ruff: noqa: F811

import pytest
from test_curation import alpha_id
from test_publication import publishing  # noqa: F401
from test_restore_preparation import setup_restore

from domain_kg.errors import DomainError
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.operations.restore_activation import RestoreActivation
from domain_kg.operations.restore_authority import RestoreAuthority
from domain_kg.operations.restore_control import RestoreControl
from domain_kg.operations.restore_human_history import RestoreHumanHistory
from domain_kg.operations.restore_preparation import RestorePreparation
from domain_kg.operations.restore_projections import RestoreProjections
from domain_kg.revisions.models import CurationCommand
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


async def test_restored_suppression_hides_node_until_new_authorized_reversal(
    publishing, team, catalog
):
    _, editor, queue, coordinator, _, _ = publishing

    async def suppress(source):
        alpha = await alpha_id(queue, editor, source.kg_id)
        await RevisionService(queue, coordinator.providers).curate_node(
            editor,
            CurationCommand(
                kg_id=source.kg_id,
                entity_id=alpha,
                action="suppress",
                expected_revision=0,
                source_ids=(source.material_id,),
                reason="Keep this node hidden in restored KGs",
                idempotency_key="backup-node-suppression",
                dry_run=False,
            ),
        )

    source, _, job, target, _ = await setup_restore(
        publishing, team, catalog, before_backup=suppress
    )
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
        alpha = next(
            row.observation.entity.id
            for row in staged.sources[0].document.rows
            if row.observation and row.observation.name == "Alpha"
        )
        with pytest.raises(DomainError):
            await GraphService(queue).get_node(access, target.target.kg_id, alpha)
        receipt = await RevisionService(queue, coordinator.providers).curate_node(
            access,
            CurationCommand(
                kg_id=target.target.kg_id,
                entity_id=alpha,
                action="restore",
                expected_revision=1,
                source_ids=(source.material_id,),
                reason="New owner reviewed this hidden node",
                idempotency_key="restored-node-unsuppress",
                dry_run=False,
            ),
        )
        assert receipt.target_revision == 2
        assert (
            await GraphService(queue).get_node(access, target.target.kg_id, alpha)
        ).name == "Alpha"
    finally:
        await staged.binding.stores.vectors.client.delete_collection(
            staged.binding.stores.vectors.collection
        )
        await staged.binding.stores.graph.driver.execute_query(
            "MATCH (n {org_id:$org,kg_id:$kg}) DETACH DELETE n",
            org=str(target.target.org_id),
            kg=str(target.target.kg_id),
        )
