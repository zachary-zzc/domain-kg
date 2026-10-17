"""Failed read-back or a failed Mongo commit leaves no target grant or accepted view."""
# ruff: noqa: F811

import pytest
from pymongo.asynchronous.collection import AsyncCollection
from qdrant_client import models
from test_publication import publishing  # noqa: F401
from test_restore_preparation import setup_restore

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.operations.restore_activation import RestoreActivation
from domain_kg.operations.restore_authority import RestoreAuthority
from domain_kg.operations.restore_control import RestoreControl
from domain_kg.operations.restore_preparation import RestorePreparation
from domain_kg.operations.restore_projections import RestoreProjections
from domain_kg.revisions.publication import projection_rows

pytestmark = pytest.mark.storage


@pytest.mark.parametrize("fault", ["source_changed", "vector_missing", "commit_interrupted"])
async def test_failed_activation_never_publishes_or_grants_a_target(
    publishing, team, catalog, monkeypatch, fault
):
    source, owner, job, target, _ = await setup_restore(publishing, team, catalog)
    _, _, queue, coordinator, _, _ = publishing
    staged = await RestorePreparation(queue, coordinator.providers).prepare(job)
    try:
        await RestoreAuthority(queue, coordinator.providers).stage(job, staged)
        await RestoreControl(queue, coordinator.providers).stage(job, staged)
        parts = await RestoreProjections(queue, coordinator.providers).stage(job, staged)
        ns = {"org_id": str(target.target.org_id), "kg_id": str(target.target.kg_id)}
        if fault == "source_changed":
            await queue.database.kgs.update_one(
                {"org_id": str(owner.org_id), "id": str(source.kg_id)},
                {"$inc": {"visibility_revision": 1}},
            )
        elif fault == "vector_missing":
            row = projection_rows(staged.sources[0].document, parts[0].report.batch)[0]
            await staged.binding.stores.vectors.client.delete(
                staged.binding.stores.vectors.collection,
                points_selector=models.PointIdsList(points=[row["projection_id"]]),
                wait=True,
            )
        else:
            insert = AsyncCollection.insert_one

            async def interrupted(collection, document, *args, **kwargs):
                if collection.name == "kg_grants" and document.get("kg_id") == ns["kg_id"]:
                    raise DomainError(
                        ErrorCode.BACKEND_UNAVAILABLE, "Synthetic transaction interruption."
                    )
                return await insert(collection, document, *args, **kwargs)

            monkeypatch.setattr(AsyncCollection, "insert_one", interrupted)
        with pytest.raises(DomainError):
            await RestoreActivation(queue, coordinator.providers).activate(job, staged, parts)
        assert (
            await queue.database.kgs.find_one({"org_id": ns["org_id"], "id": ns["kg_id"]}) is None
        )
        for table in (
            "kg_grants",
            "publication_manifests",
            "projection_attempts",
            "kg_provider_settings",
        ):
            assert await queue.database[table].count_documents(ns) == 0
        assert (
            await queue.database.restore_results.count_documents(
                {"org_id": str(owner.org_id), "kg_id": str(source.kg_id), "id": str(job.target_id)}
            )
            == 0
        )
        raw = await queue.database.jobs.find_one(queue._scope(job))
        assert raw["stage"] != "ready" and not await queue.accepted(job)
    finally:
        await staged.binding.stores.vectors.client.delete_collection(
            staged.binding.stores.vectors.collection
        )
        await staged.binding.stores.graph.driver.execute_query(
            "MATCH (n {org_id:$org,kg_id:$kg}) DETACH DELETE n",
            org=str(target.target.org_id),
            kg=str(target.target.kg_id),
        )
