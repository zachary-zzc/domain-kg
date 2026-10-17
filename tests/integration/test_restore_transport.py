"""Public restore intent stays inactive until independently approved and verified."""
# ruff: noqa: F811

import httpx
import pytest
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_publication import publishing  # noqa: F401
from test_restore_preparation import setup_restore
from test_transport_authorization import settings_for

from domain_kg.app import create_app
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.operations.restore import RestoreService
from domain_kg.operations.restore_targets import RestoreTargets

pytestmark = pytest.mark.storage


async def test_owner_restore_console_api_and_new_namespace_approval(
    publishing, team, catalog, issuer_fixture
):
    source, owner, existing, _, backup = await setup_restore(publishing, team, catalog)
    _, _, queue, coordinator, _, _ = publishing
    await queue.cancel(owner, existing.id, idempotency_key="skip-private-restore-fixture")
    profile = next(iter(coordinator.providers.registry.profiles.values()))
    identities, _, principals, _, org, _ = team
    _, state, transport, _ = issuer_fixture
    state["issuer"] = principals["owner"].issuer
    async with httpx.AsyncClient(transport=transport) as oidc:
        app = create_app(
            settings_for(state["issuer"]),
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
                headers={"Origin": "http://localhost:8000", "x-csrf-token": ticket.csrf},
                cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
            ) as browser:
                root = f"/admin/kgs/{source.kg_id}/backups/jobs/{backup.job_id}"
                page = await browser.get(root)
                assert page.status_code == 200 and "Restore to a fresh KG" in page.text
                fields = {
                    "csrf_token": ticket.csrf,
                    "expected_published_revision": "1",
                    "placement_id": str(catalog[3].id),
                    "provider_profile_id": str(profile.id),
                    "name": "Console recovery",
                    "idempotency_key": "public-restore",
                }
                assert (
                    await browser.post(root + "/restore", data=fields | {"csrf_token": "wrong"})
                ).status_code == 403
                queued = await browser.post(root + "/restore", data=fields)
                assert queued.status_code == 303
                api = f"/api/v1/kgs/{source.kg_id}/restores"
                command = {key: value for key, value in fields.items() if key != "csrf_token"}
                command.update(
                    kg_id=str(source.kg_id),
                    backup_job_id=str(backup.job_id),
                    expected_published_revision=1,
                )
                replay = await browser.post(api, json=command)
                assert replay.status_code == 202
                job_id = replay.json()["job_id"]
                assert queued.headers["location"].endswith(job_id)
                info = await browser.get(api + f"/jobs/{job_id}")
                assert info.status_code == 200 and not info.json()["provider_approved"]
                target_kg = info.json()["target"]["kg_id"]
                assert not await queue.database.kgs.find_one({"id": target_kg})
                status = await browser.get(queued.headers["location"])
                assert target_kg in status.text and "approval" in status.text.lower()
                assert not any(
                    secret in info.text
                    for secret in ("connection_ref", "allowed_org_ids", "endpoint", "api_key")
                )
                job = await queue.claim("public-restore", kinds=("restore",))
                target = await RestoreTargets(queue).get(owner, source.kg_id, job.id)
                registry = coordinator.providers.registry
                registry.profiles[profile.id] = registry.profiles[profile.id].model_copy(
                    update={
                        "allowed_namespaces": (
                            *registry.profiles[profile.id].allowed_namespaces,
                            target.target,
                        ),
                        "allow_restricted": True,
                    }
                )
                assert (await browser.get(api + f"/jobs/{job_id}")).json()["provider_approved"]
                await JobRunner(
                    queue,
                    configured_pipeline(queue, coordinator.providers),
                    operations=RestoreService(queue, coordinator.providers),
                ).run_job(job)
                completed = await browser.get(queued.headers["location"])
                assert completed.status_code == 200 and 'data-stage="ready"' in completed.text
                assert f"/admin/kgs/{target_kg}/search" in completed.text
                for role in ("reader", "editor"):
                    denied = await app.state.identity.sessions.create(principals[role], org)
                    browser.cookies.set("domain_kg_session", denied.session_id)
                    browser.cookies.set("domain_kg_csrf", denied.csrf)
                    browser.headers["x-csrf-token"] = denied.csrf
                    assert (await browser.post(api, json=command)).status_code in (403, 404)
                    assert (await browser.get(api + f"/jobs/{job_id}")).status_code in (403, 404)
