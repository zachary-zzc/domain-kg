"""One real owner restore job runs through the native serial runner."""
# ruff: noqa: F811

import httpx
import pytest
from test_curation import alpha_id
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_publication import publishing  # noqa: F401
from test_restore_preparation import setup_restore
from test_transport_authorization import settings_for

from domain_kg.app import create_app
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.operations.restore_results import RestoreResults
from domain_kg.operations.restore_stores import RestoreStores

pytestmark = pytest.mark.storage


async def test_serial_kg_restore_handler_accepts_only_fully_verified_target(
    publishing, team, catalog, monkeypatch, issuer_fixture
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
        target_quota = await queue.database.kg_quotas.find_one(
            {"org_id": str(source.org_id), "kg_id": str(target.target.kg_id)}
        )
        assert target_quota["reserved_bytes"] == 0 and target_quota["committed_bytes"] > 0
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
        identities, _, principals, _, org, _ = team
        _, oidc_state, oidc_transport, _ = issuer_fixture
        oidc_state["issuer"] = principals["owner"].issuer
        async with httpx.AsyncClient(transport=oidc_transport) as oidc:
            app = create_app(
                settings_for(oidc_state["issuer"]),
                identity_repository=identities,
                oidc_http_client=oidc,
                catalog_service=catalog[0],
                providers=coordinator.providers,
            )
            async with app.router.lifespan_context(app):
                ticket = await app.state.identity.sessions.create(principals["owner"], org)
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://localhost:8000",
                    cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
                ) as browser:
                    path = f"/admin/kgs/{source.kg_id}/jobs/{job.id}"
                    page = await browser.get(path)
                    assert page.status_code == 200
                    status_fragment = page.text.split('id="job-status"', 1)[1].split(
                        "</section>", 1
                    )[0]
                    assert f'href="/admin/kgs/{target.target.kg_id}/search"' in status_fragment
                    reader = await app.state.identity.sessions.create(principals["reader"], org)
                    browser.cookies.clear()
                    browser.cookies.set("domain_kg_session", reader.session_id)
                    hidden = await browser.get(path)
                    assert hidden.status_code == 404 and str(target.target.kg_id) not in hidden.text
    finally:
        await binding.stores.vectors.client.delete_collection(binding.stores.vectors.collection)
        await binding.stores.graph.driver.execute_query(
            "MATCH (n {org_id:$org,kg_id:$kg}) DETACH DELETE n",
            org=str(target.target.org_id),
            kg=str(target.target.kg_id),
        )
