"""A partial projection or revoked owner cannot activate an otherwise staged target."""
# ruff: noqa: F811

import pytest
from test_publication import publishing  # noqa: F401
from test_restore_preparation import setup_restore

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.operations.restore_authority import RestoreAuthority
from domain_kg.operations.restore_control import RestoreControl
from domain_kg.operations.restore_preparation import RestorePreparation
from domain_kg.operations.restore_projections import RestoreProjections
from domain_kg.storage.neo4j import GraphProjection
from domain_kg.storage.qdrant import VectorProjection

pytestmark = pytest.mark.storage


@pytest.mark.parametrize("fault", ["vector_failure", "owner_revoked"])
async def test_incomplete_or_revoked_projection_never_activates_target(
    publishing, team, catalog, monkeypatch, fault
):
    source, owner, job, target, _ = await setup_restore(publishing, team, catalog)
    _, _, queue, coordinator, _, _ = publishing
    staged = await RestorePreparation(queue, coordinator.providers).prepare(job)
    try:
        await RestoreAuthority(queue, coordinator.providers).stage(job, staged)
        await RestoreControl(queue, coordinator.providers).stage(job, staged)
        original = GraphProjection.write

        async def graph_then_revoke(projection, rows):
            await original(projection, rows)
            await queue.database.members.update_one(
                {"org_id": str(owner.org_id), "id": str(team[3]["owner"].id)},
                {"$set": {"suspended": True}},
            )

        async def broken_vector(*args, **kwargs):
            raise DomainError(
                ErrorCode.BACKEND_UNAVAILABLE, "Synthetic vector outage.", retryable=True
            )

        if fault == "owner_revoked":
            monkeypatch.setattr(GraphProjection, "write", graph_then_revoke)
        else:
            monkeypatch.setattr(VectorProjection, "write", broken_vector)
        with pytest.raises(DomainError):
            await RestoreProjections(queue, coordinator.providers).stage(job, staged)
        ns = {"org_id": str(target.target.org_id), "kg_id": str(target.target.kg_id)}
        assert (
            await queue.database.kgs.find_one({"org_id": ns["org_id"], "id": ns["kg_id"]}) is None
        )
        assert await queue.database.publication_manifests.count_documents(ns) == 0
        assert await queue.database.kg_grants.count_documents(ns) == 0
        graph = staged.binding.stores.graph.driver
        rows, _, _ = await graph.execute_query(
            "MATCH (n:ScopedProjection {org_id:$org,kg_id:$kg}) RETURN count(n) AS count",
            org=ns["org_id"],
            kg=ns["kg_id"],
        )
        assert rows[0]["count"] > 0
        count = await staged.binding.stores.vectors.client.count(
            staged.binding.stores.vectors.collection, exact=True
        )
        assert count.count == 0
        source_row = await queue.database.kgs.find_one(
            {"org_id": str(owner.org_id), "id": str(source.kg_id)}
        )
        assert source_row["published_revision"] == 1 and source_row["state"] == "ready"
    finally:
        await staged.binding.stores.vectors.client.delete_collection(
            staged.binding.stores.vectors.collection
        )
        await staged.binding.stores.graph.driver.execute_query(
            "MATCH (n {org_id:$org,kg_id:$kg}) DETACH DELETE n",
            org=str(target.target.org_id),
            kg=str(target.target.kg_id),
        )
