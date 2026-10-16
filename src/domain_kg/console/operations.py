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
from domain_kg.operations.projections import RepairCommand
from domain_kg.transport.runtime import ServiceRuntime


def operations_router(runtime: ServiceRuntime) -> APIRouter:
    router = APIRouter()

    @router.get("/kgs/{kg_id}/operations")
    async def operations(request: Request, kg_id: UUID) -> HTMLResponse:
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
                "command_key": str(uuid4()),
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
