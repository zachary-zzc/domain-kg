"""Owner export forms and downloads preserve the shared evidence read boundary."""

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from domain_kg.console.export_forms import export_command
from domain_kg.console.material_commands import command_context, redirect
from domain_kg.console.rendering import page
from domain_kg.console.workspace import filters_for, read_context
from domain_kg.identity.models import Action
from domain_kg.operations.export import ExportService
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.transport.runtime import ServiceRuntime


def export_router(runtime: ServiceRuntime) -> APIRouter:
    router = APIRouter()

    @router.post("/kgs/{kg_id}/operations/export")
    async def export(request: Request, kg_id: UUID) -> RedirectResponse:
        fields, _, current = await command_context(runtime, request, kg_id, Action.CONFIGURE)
        receipt = await OwnerOperations(runtime.queue).submit(
            current, export_command(kg_id, fields)
        )
        return redirect(f"/admin/kgs/{kg_id}/jobs/{receipt.job_id}")

    @router.get("/kgs/{kg_id}/exports/jobs/{job_id}")
    async def export_detail(request: Request, kg_id: UUID, job_id: UUID) -> HTMLResponse:
        session, access, scope = await read_context(runtime, request, kg_id, filters_for(True))
        current = await runtime.policy.revalidate(access, action=Action.CONFIGURE)
        manifest = await ExportService(runtime.queue).manifest(current, kg_id, job_id)
        return await page(
            runtime,
            session,
            current,
            "export.html",
            {
                "title": "Scoped export",
                "kg": await runtime.catalog.get_kg(current, kg_id),
                "manifest": manifest,
            },
            scope=scope,
        )

    return router
