"""A fresh target becomes readable only with verified projections and a new owner grant."""
# ruff: noqa: F811

import pytest
from test_publication import publishing  # noqa: F401
from test_restore_preparation import setup_restore

from domain_kg.errors import DomainError
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.operations.restore_authority import RestoreAuthority
from domain_kg.operations.restore_control import RestoreControl
from domain_kg.operations.restore_preparation import RestorePreparation
from domain_kg.operations.restore_projections import RestoreProjections

pytestmark = pytest.mark.storage


async def test_verified_native_restore_activates_once_with_only_current_owner_access(
    publishing, team, catalog
):
    from domain_kg.operations.restore_activation import RestoreActivation

    source, owner, job, target, _ = await setup_restore(publishing, team, catalog)
    _, _, queue, coordinator, _, _ = publishing
    staged = await RestorePreparation(queue, coordinator.providers).prepare(job)
    try:
        await RestoreAuthority(queue, coordinator.providers).stage(job, staged)
        await RestoreControl(queue, coordinator.providers).stage(job, staged)
        parts = await RestoreProjections(queue, coordinator.providers).stage(job, staged)
        result = await RestoreActivation(queue, coordinator.providers).activate(job, staged, parts)
        assert result.target == target.target and result.job_id == job.id
        current = await queue.policy.resolve_access(
            team[2]["owner"], [target.target.kg_id], Action.READ
        )
        alpha = next(
            row.observation.entity.id
            for row in staged.sources[0].document.rows
            if row.observation and row.observation.name == "Alpha"
        )
        node = await GraphService(queue).get_node(current, target.target.kg_id, alpha)
        assert node.name == "Alpha" and node.support_count >= 1
        for role in ("reader", "editor"):
            with pytest.raises(DomainError):
                await queue.policy.resolve_access(team[2][role], [target.target.kg_id], Action.READ)
        grants = await queue.database.kg_grants.find(
            {"org_id": str(current.org_id), "kg_id": str(target.target.kg_id)}
        ).to_list()
        assert len(grants) == 1 and grants[0]["subject_id"] == str(team[3]["owner"].id)
        source_owner = await queue.policy.resolve_access(
            team[2]["owner"], [source.kg_id], Action.RESTORE
        )
        assert (await queue.get(source_owner, job.id)).stage == "ready"
        assert await queue.accepted(job)
        await queue.finish(job)
        assert (
            await RestoreActivation(queue, coordinator.providers).activate(job, staged, parts)
            == result
        )
        original = await queue.database.kgs.find_one(
            {"org_id": str(owner.org_id), "id": str(source.kg_id)}
        )
        assert original["published_revision"] == 1 and original["state"] == "ready"
    finally:
        await staged.binding.stores.vectors.client.delete_collection(
            staged.binding.stores.vectors.collection
        )
        await staged.binding.stores.graph.driver.execute_query(
            "MATCH (n {org_id:$org,kg_id:$kg}) DETACH DELETE n",
            org=str(target.target.org_id),
            kg=str(target.target.kg_id),
        )
