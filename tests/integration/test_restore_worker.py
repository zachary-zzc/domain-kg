"""One real owner restore job runs through the native serial runner."""
# ruff: noqa: F811

import httpx
import pytest
from test_curation import alpha_id
from test_publication import publishing  # noqa: F401
from test_restore_preparation import setup_restore

from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.operations.restore_results import RestoreResults
from domain_kg.operations.restore_stores import RestoreStores

pytestmark = pytest.mark.storage


async def test_serial_kg_restore_handler_accepts_only_fully_verified_target(
    publishing, team, catalog, monkeypatch
):
    from domain_kg.operations.restore import RestoreService

    source, owner, job, target, _ = await setup_restore(publishing, team, catalog)
    _, _, queue, coordinator, _, _ = publishing

    async def forbidden(*args, **kwargs):
        pytest.fail("unchanged native restore worker must not call any model")

    monkeypatch.setattr(coordinator.providers, "transport", httpx.MockTransport(forbidden))
    binding = await RestoreStores(queue, coordinator.providers.registry).provision(job)
    try:
        runner = JobRunner(
            queue,
            configured_pipeline(queue, coordinator.providers),
            operations=RestoreService(queue, coordinator.providers),
        )
        await runner.run_job(job)
        owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.RESTORE)
        status = await queue.get(owner, job.id)
        assert status.stage == "ready", status.warnings
        result = await RestoreResults(queue).get(owner, source.kg_id, job.id)
        assert result.target == target.target
        access = await queue.policy.resolve_access(
            team[2]["owner"], [target.target.kg_id], Action.READ
        )
        alpha = await alpha_id(queue, access, target.target.kg_id)
        assert (
            await GraphService(queue).get_node(access, target.target.kg_id, alpha)
        ).name == "Alpha"
        await RestoreService(queue, coordinator.providers).run(job)  # Lost response replay.
    finally:
        await binding.stores.vectors.client.delete_collection(binding.stores.vectors.collection)
        await binding.stores.graph.driver.execute_query(
            "MATCH (n {org_id:$org,kg_id:$kg}) DETACH DELETE n",
            org=str(target.target.org_id),
            kg=str(target.target.kg_id),
        )
