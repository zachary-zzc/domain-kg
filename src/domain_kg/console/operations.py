"""Owner projection work and separately authorized operator capability checks."""

from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from domain_kg.console.forms import read_form
from domain_kg.console.material_commands import command_context, invalid_form, redirect
from domain_kg.console.rendering import browser_session, page
from domain_kg.console.workspace import filters_for, read_context
from domain_kg.errors import inaccessible_reference
from domain_kg.identity.models import Action
from domain_kg.models import PageRequest
from domain_kg.operations.audit import AuditTrail
from domain_kg.operations.models import MaintenanceCommand, RetentionCommand
from domain_kg.operations.projections import RepairCommand
from domain_kg.transport.runtime import ServiceRuntime


def operations_router(runtime: ServiceRuntime) -> APIRouter:
    router = APIRouter()

    @router.get("/kgs/{kg_id}/operations")
    async def operations(
        request: Request, kg_id: UUID, audit_cursor: str | None = None
    ) -> HTMLResponse:
        session, access, scope = await read_context(runtime, request, kg_id, filters_for(True))
        access = await runtime.policy.revalidate(access, action=Action.CONFIGURE)
        result = await runtime.projections.status(access, kg_id)
        return await page(
            runtime,
            session,
            access,
            "operations.html",
            {
                "title": "Projection operations",
                "kg": await runtime.catalog.get_kg(access, kg_id),
                "status": result,
                "usage": await runtime.usage.get(access, kg_id),
                "audit": await AuditTrail(runtime.queue).list(
                    access, kg_id, PageRequest(limit=20, cursor=audit_cursor)
                ),
                "command_key": str(uuid4()),
                "curation_repair_key": str(uuid4()),
                "backup_key": str(uuid4()),
                "export_key": str(uuid4()),
                "retention_key": str(uuid4()),
                "retention_policy": runtime.retention_policy,
            },
            scope=scope,
        )

    @router.post("/kgs/{kg_id}/operations/repair")
    async def repair(request: Request, kg_id: UUID) -> RedirectResponse:
        fields, _, access = await command_context(runtime, request, kg_id, Action.CONFIGURE)
        try:
            command = RepairCommand.model_validate(fields)
        except (ValueError, ValidationError):
            raise invalid_form() from None
        result = await runtime.projections.repair(
            access, kg_id, command.material_id, idempotency_key=command.idempotency_key
        )
        return redirect(f"/admin/kgs/{kg_id}/jobs/{result.job_id}")

    @router.post("/kgs/{kg_id}/operations/repair-curation")
    async def repair_curation(request: Request, kg_id: UUID) -> RedirectResponse:
        fields, _, access = await command_context(runtime, request, kg_id, Action.CONFIGURE)
        try:
            command = MaintenanceCommand.model_validate(
                fields
                | {
                    "kg_id": kg_id,
                    "expected_published_revision": int(
                        fields.get("expected_published_revision", "")
                    ),
                }
            )
        except (ValueError, ValidationError):
            raise invalid_form() from None
        result = await runtime.projections.repair_curation(access, command)
        return redirect(f"/admin/kgs/{kg_id}/jobs/{result.job_id}")

    @router.post("/kgs/{kg_id}/operations/retention")
    async def retention(request: Request, kg_id: UUID) -> RedirectResponse:
        fields, _, access = await command_context(runtime, request, kg_id, Action.CONFIGURE)
        try:
            command = RetentionCommand.model_validate(
                fields
                | {
                    "kg_id": kg_id,
                    "expected_published_revision": int(
                        fields.get("expected_published_revision", "")
                    ),
                }
            )
        except (ValueError, ValidationError):
            raise invalid_form() from None
        result = await runtime.owner_operations.submit(access, command)
        return redirect(f"/admin/kgs/{kg_id}/jobs/{result.job_id}")

    @router.get("/infrastructure")
    async def infrastructure(request: Request, cursor: str | None = None) -> HTMLResponse:
        session = await browser_session(runtime, request)
        access = await runtime.access(
            session.principal, (), Action.REGISTER_BACKEND, session.member.org_id
        )
        result = await runtime.catalog.registry.inventory(
            access, "backends", PageRequest(cursor=cursor)
        )
        return await page(
            runtime,
            session,
            access,
            "infrastructure.html",
            {
                "title": "Registered backends",
                "inventory": result,
                "health": None,
            },
        )

    @router.post("/infrastructure/{backend_id}/validate")
    async def validate(request: Request, backend_id: UUID) -> HTMLResponse:
        fields = await read_form(request)
        session = await browser_session(runtime, request, csrf=fields.pop("csrf_token", ""))
        if fields:
            raise invalid_form()
        access = await runtime.access(
            session.principal, (), Action.REGISTER_BACKEND, session.member.org_id
        )
        records = await runtime.catalog.registry.list_backends(access)
        target = next((record for record in records if record.backend_id == backend_id), None)
        if target is None:
            raise inaccessible_reference()
        result = await runtime.catalog.registry.probe(
            backend_id, await runtime.catalog.registry.connection(backend_id, target.kind)
        )
        return await page(
            runtime,
            session,
            access,
            "infrastructure.html",
            {
                "title": "Registered backend health",
                "inventory": None,
                "health": result,
            },
        )

    return router
