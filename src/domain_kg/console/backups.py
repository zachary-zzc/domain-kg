"""Owner backup admission and manifest inspection use the shared verified service."""

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from domain_kg.console.material_commands import command_context, invalid_form, redirect
from domain_kg.console.rendering import page
from domain_kg.console.workspace import filters_for, read_context
from domain_kg.identity.models import Action
from domain_kg.operations.backup import BackupService
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import BackupCommand
from domain_kg.transport.runtime import ServiceRuntime


def backup_router(runtime: ServiceRuntime) -> APIRouter:
    router = APIRouter()
    service = BackupService(runtime.queue)

    @router.post("/kgs/{kg_id}/operations/backup")
    async def backup(request: Request, kg_id: UUID) -> RedirectResponse:
        fields, _, access = await command_context(runtime, request, kg_id, Action.BACKUP)
        if set(fields) != {"expected_published_revision", "retention_days", "idempotency_key"}:
            raise invalid_form()
        try:
            command = BackupCommand(
                kg_id=kg_id,
                expected_published_revision=int(fields["expected_published_revision"]),
                retention_days=int(fields["retention_days"]),
                idempotency_key=fields["idempotency_key"],
            )
        except (ValueError, ValidationError):
            raise invalid_form() from None
        result = await OwnerOperations(runtime.queue).submit(access, command)
        return redirect(f"/admin/kgs/{kg_id}/jobs/{result.job_id}")

    @router.get("/kgs/{kg_id}/backups/jobs/{job_id}")
    async def backup_detail(
        request: Request, kg_id: UUID, job_id: UUID, index: int = 0
    ) -> HTMLResponse:
        session, access, scope = await read_context(runtime, request, kg_id, filters_for(True))
        current = await runtime.policy.revalidate(access, action=Action.BACKUP)
        manifest = await service.manifest(current, kg_id, job_id)
        result = await service.parts(current, kg_id, manifest.backup_id, index)
        return await page(
            runtime,
            session,
            current,
            "backup.html",
            {
                "title": "Retained backup",
                "kg": await runtime.catalog.get_kg(current, kg_id),
                "manifest": manifest,
                "parts": result,
                "index": index,
            },
            scope=scope,
        )

    return router
