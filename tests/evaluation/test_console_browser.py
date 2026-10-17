"""Opt-in, temporary loopback preview over synthetic fixtures, never a production login."""
# ruff: noqa: F811

import asyncio
import json
import os
from pathlib import Path
from typing import Literal

import httpx
import pytest
import uvicorn
from fastapi.responses import RedirectResponse
from test_curation import alpha_id
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.app import create_app
from domain_kg.identity.models import Action
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.operations.backup import BackupService
from domain_kg.operations.executor import OwnerJobExecutor
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import BackupCommand, RestoreCommand
from domain_kg.operations.restore import RestoreService
from domain_kg.operations.restore_targets import RestoreTargets
from domain_kg.settings import Settings
from domain_kg.worker import serve

PROFILE_PATH = Path(__file__).resolve().parents[2] / "config/laptop-smoke.json"

pytestmark = [
    pytest.mark.storage,
    pytest.mark.skipif(
        os.environ.get("DOMAIN_KG_BROWSER_PREVIEW") != "1",
        reason="manual loopback browser preview is opt-in",
    ),
]


async def test_temporary_console_browser_preview(publishing, team, catalog, issuer_fixture):
    _, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    alpha = await alpha_id(queue, editor, job.kg_id)
    identities, _, principals, _, org, _ = team
    owner = await queue.policy.resolve_access(principals["owner"], [job.kg_id], Action.RESTORE)
    operations = OwnerOperations(queue)
    backup = await operations.submit(
        owner,
        BackupCommand(
            kg_id=job.kg_id, expected_published_revision=1, idempotency_key="preview-backup"
        ),
    )
    await BackupService(queue).run(await queue.claim("preview-backup", kinds=("backup",)))
    registry = coordinator.providers.registry
    profile = next(iter(registry.profiles.values()))
    recovery = await operations.submit(
        owner,
        RestoreCommand(
            kg_id=job.kg_id,
            expected_published_revision=1,
            backup_job_id=backup.job_id,
            placement_id=catalog[3].id,
            provider_profile_id=profile.id,
            name="Restored example",
            idempotency_key="preview-restore",
        ),
    )
    target = await RestoreTargets(queue).get(owner, job.kg_id, recovery.job_id)
    registry.profiles[profile.id] = profile.model_copy(
        update={
            "allowed_namespaces": (*profile.allowed_namespaces, target.target),
            "allow_restricted": True,
        }
    )
    restore_job = await queue.claim("preview-restore", kinds=("restore",))
    await JobRunner(
        queue,
        configured_pipeline(queue, coordinator.providers),
        operations=RestoreService(queue, coordinator.providers),
    ).run_job(restore_job)
    refreshed = await queue.policy.resolve_access(principals["owner"], [job.kg_id], Action.RESTORE)
    assert (await queue.get(refreshed, recovery.job_id)).stage == "ready"
    _, state, transport, _ = issuer_fixture
    state["issuer"] = principals["owner"].issuer
    async with httpx.AsyncClient(transport=transport) as oidc:
        app = create_app(
            Settings(
                public_url="http://localhost:28080",
                oidc_issuer=state["issuer"],
                oidc_client_id="console",
            ),
            identity_repository=identities,
            oidc_http_client=oidc,
            catalog_service=catalog[0],
            providers=coordinator.providers,
        )
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=28080,
                lifespan="off",
                log_level="warning",
                access_log=False,
                timeout_graceful_shutdown=5,
            )
        )

        # These fixture routes exist only in this opt-in pytest process and synthetic namespace.
        @app.get("/__smoke__/start/{role}")
        async def start(
            role: str, view: Literal["catalog", "search", "operations", "restore_job"] = "catalog"
        ):
            assert role in ("owner", "editor", "reader")
            ticket = await app.state.identity.sessions.create(principals[role], org)
            destination = (
                f"/admin/kgs/{job.kg_id}/jobs/{recovery.job_id}"
                if view == "restore_job"
                else "/admin"
                if view == "catalog"
                else f"/admin/kgs/{job.kg_id}/{view}"
            )
            response = RedirectResponse(destination, status_code=303)
            response.set_cookie(
                "domain_kg_session", ticket.session_id, httponly=True, samesite="lax", path="/"
            )
            response.set_cookie("domain_kg_csrf", ticket.csrf, samesite="lax", path="/")
            return response

        @app.get("/__smoke__/fixture")
        async def fixture_links():
            # Synthetic fixture identifiers only; no source payload, credentials or configuration.
            return {
                "kg_id": str(job.kg_id),
                "material_id": str(source.material_id),
                "node_id": str(alpha),
                "restored_kg_id": str(target.target.kg_id),
                "restore_job_id": str(recovery.job_id),
            }

        @app.get("/__smoke__/finish")
        async def finish():
            server.should_exit = True
            return {"finished": True}

        smoke_routes = app.router.routes[-3:]
        del app.router.routes[-3:]
        app.router.routes[0:0] = smoke_routes
        async with app.router.lifespan_context(app):
            serving = asyncio.create_task(server.serve())
            working = asyncio.create_task(
                serve(
                    queue,
                    JobRunner(
                        queue,
                        configured_pipeline(queue, coordinator.providers),
                        operations=OwnerJobExecutor(queue),
                    ),
                    once=False,
                    worker_id="synthetic-preview-operations",
                    operations_only=True,
                )
            )
            try:
                for _ in range(100):
                    if server.started:
                        break
                    await asyncio.sleep(0.05)
                assert server.started
                print(
                    "Browser preview ready: http://localhost:28080/__smoke__/start/owner",
                    flush=True,
                )
                configuration = json.loads(await asyncio.to_thread(PROFILE_PATH.read_bytes))
                lifetime = int(configuration["tests"]["preview_seconds"])
                assert 1 <= lifetime <= 900
                done, _ = await asyncio.wait(
                    (serving, working), timeout=lifetime, return_when=asyncio.FIRST_COMPLETED
                )
                if working in done:
                    await working  # Do not advertise a dead preview worker.
            finally:
                server.should_exit = True
                working.cancel()
                await asyncio.gather(working, return_exceptions=True)
                await asyncio.wait_for(serving, timeout=10)
