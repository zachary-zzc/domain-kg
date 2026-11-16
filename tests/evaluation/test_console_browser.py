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
from test_curation import alpha_id, publish_curation
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.app import create_app
from domain_kg.identity.models import Action
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.materials.models import AddMaterial
from domain_kg.operations.backup import BackupService
from domain_kg.operations.executor import OwnerJobExecutor
from domain_kg.operations.generation import GenerationService
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.maintenance import MaintenanceService
from domain_kg.operations.models import BackupCommand, RestoreCommand
from domain_kg.operations.restore import RestoreService
from domain_kg.operations.restore_cleanup import RestoreCleanupService
from domain_kg.operations.restore_targets import RestoreTargets
from domain_kg.operations.retention import RetentionService
from domain_kg.revisions.models import NodeChanges, NodeUpdate
from domain_kg.revisions.service import RevisionService
from domain_kg.settings import Settings

PROFILE_PATH = Path(__file__).resolve().parents[2] / "config/laptop-smoke.json"

pytestmark = [
    pytest.mark.storage,
    pytest.mark.skipif(
        os.environ.get("DOMAIN_KG_BROWSER_PREVIEW") != "1",
        reason="manual loopback browser preview is opt-in",
    ),
]


async def test_temporary_console_browser_preview(publishing, team, catalog, issuer_fixture):
    configuration = json.loads(await asyncio.to_thread(PROFILE_PATH.read_bytes))
    import_demo = os.environ.get("DOMAIN_KG_BROWSER_IMPORTS") == "1"
    full_restore = configuration["tests"].get("preview_restore_demo", False)
    assert type(full_restore) is bool
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
    if full_restore:
        restore_job = await queue.claim("preview-restore", kinds=("restore",))
        await JobRunner(
            queue,
            configured_pipeline(queue, coordinator.providers),
            operations=RestoreService(queue, coordinator.providers),
        ).run_job(restore_job)
    else:
        await queue.cancel(owner, recovery.job_id, idempotency_key="preview-skip-full-restore")
    editor = await queue.policy.resolve_access(principals["editor"], [job.kg_id], Action.CURATE)
    reviewed = await RevisionService(queue, coordinator.providers).update_node(
        editor,
        NodeUpdate(
            kg_id=job.kg_id,
            entity_id=alpha,
            expected_revision=0,
            source_ids=(source.material_id,),
            changes=NodeChanges(name="Reviewed Alpha"),
            reason="Synthetic review for the repair demonstration",
            idempotency_key="preview-reviewed-name",
        ),
    )
    await publish_curation(queue, coordinator, editor, reviewed)
    refreshed = await queue.policy.resolve_access(principals["owner"], [job.kg_id], Action.RESTORE)
    assert (await queue.get(refreshed, recovery.job_id)).stage == (
        "ready" if full_restore else "cancelled"
    )
    abandoned = await operations.submit(
        refreshed,
        RestoreCommand(
            kg_id=job.kg_id,
            expected_published_revision=2,
            backup_job_id=backup.job_id,
            placement_id=catalog[3].id,
            provider_profile_id=profile.id,
            name="Inactive cleanup example",
            idempotency_key="preview-inactive-restore",
        ),
    )
    inactive = await RestoreTargets(queue).get(refreshed, job.kg_id, abandoned.job_id)
    await queue.cancel(refreshed, abandoned.job_id, idempotency_key="preview-cancel-inactive")
    retry_jobs = []
    if import_demo:
        from test_entity_resolution import TEXT

        for index in range(2):
            paused = await publishing[0].add(
                editor,
                AddMaterial(
                    kg_id=job.kg_id,
                    title=f"Retry demonstration {index + 1}",
                    inline_text=TEXT,
                    idempotency_key=f"preview-retry-{index}",
                ),
            )
            await queue.cancel(
                editor, paused.job_id, idempotency_key=f"preview-pause-retry-{index}"
            )
            retry_jobs.append(str(paused.job_id))
    _, state, transport, _ = issuer_fixture
    state["issuer"] = principals["owner"].issuer
    async with httpx.AsyncClient(transport=transport) as oidc:
        app = create_app(
            Settings(
                public_url="http://localhost:28080",
                oidc_issuer=state["issuer"],
                oidc_client_id="console",
                retention_config=PROFILE_PATH.with_name("retention.example.json"),
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
            role: str,
            view: Literal[
                "catalog", "search", "operations", "restore_job", "inactive_restore", "backup"
            ] = "catalog",
            lang: Literal["en", "zh-CN"] | None = None,
        ):
            assert role in ("owner", "editor", "reader")
            ticket = await app.state.identity.sessions.create(principals[role], org)
            destination = (
                f"/admin/kgs/{job.kg_id}/backups/jobs/{backup.job_id}"
                if view == "backup"
                else f"/admin/kgs/{job.kg_id}/jobs/{recovery.job_id}"
                if view == "restore_job"
                else f"/admin/kgs/{job.kg_id}/jobs/{abandoned.job_id}"
                if view == "inactive_restore"
                else "/admin"
                if view == "catalog"
                else f"/admin/kgs/{job.kg_id}/{view}"
            )
            response = RedirectResponse(destination, status_code=303)
            response.set_cookie(
                "domain_kg_session", ticket.session_id, httponly=True, samesite="lax", path="/"
            )
            response.set_cookie("domain_kg_csrf", ticket.csrf, samesite="lax", path="/")
            if lang is not None:
                response.set_cookie(
                    "domain_kg_language", lang, httponly=True, samesite="lax", path="/admin"
                )
            return response

        @app.get("/__smoke__/fixture")
        async def fixture_links():
            # Synthetic fixture identifiers only; no source payload, credentials or configuration.
            return {
                "kg_id": str(job.kg_id),
                "material_id": str(source.material_id),
                "node_id": str(alpha),
                "restored_kg_id": str(target.target.kg_id) if full_restore else None,
                "restore_target_kg_id": str(target.target.kg_id),
                "full_restore_demo": full_restore,
                "restore_job_id": str(recovery.job_id),
                "inactive_restore_job_id": str(abandoned.job_id),
                "inactive_kg_id": str(inactive.target.kg_id),
                "backup_job_id": str(backup.job_id),
                "placement_id": str(catalog[3].id),
                "provider_profile_id": str(profile.id),
                "curation_id": str(reviewed.revision_id),
                "import_demo": import_demo,
                "retry_job_ids": retry_jobs,
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

            class PreviewOperations:
                async def run(self, leased):
                    if leased.kind == "generation":
                        await GenerationService(queue).run(leased)
                    elif leased.kind == "restore_cleanup":
                        await RestoreCleanupService(queue).run(leased)
                    elif leased.kind == "maintenance":
                        await MaintenanceService(queue).run(leased)
                    elif leased.kind == "retention":
                        await RetentionService(queue).run(leased)
                    else:
                        await OwnerJobExecutor(queue).run(leased)

            async def work():
                runner = JobRunner(
                    queue,
                    configured_pipeline(queue, coordinator.providers),
                    operations=PreviewOperations(),
                )
                while True:
                    # The optional ingestion worker uses the fixture's MockTransport provider.
                    # Its queue/router are confined to this test's synthetic namespace.
                    kinds = (
                        (
                            "ingest",
                            "import",
                            "replace",
                            "repair",
                            "restore_material",
                            "reprocess_material",
                            "curate",
                            "purge_material",
                        )
                        if import_demo
                        else ()
                    )
                    leased = await queue.claim(
                        "synthetic-preview-operations",
                        kinds=(
                            *kinds,
                            "backup",
                            "export",
                            "maintenance",
                            "retention",
                            "restore_cleanup",
                            "generation",
                        ),
                    )
                    if leased:
                        await runner.run_job(leased)
                    else:
                        await asyncio.sleep(1)

            working = asyncio.create_task(work())
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
                lifetime = int(
                    os.environ.get("DOMAIN_KG_BROWSER_PREVIEW_SECONDS", "1200")
                    if import_demo
                    else configuration["tests"]["preview_seconds"]
                )
                assert 1 <= lifetime <= (1800 if import_demo else 900)
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
