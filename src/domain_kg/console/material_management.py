"""Editor metadata and restoration controls never serve withdrawn source evidence."""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from domain_kg.console.material_commands import command_context, invalid_form, redirect
from domain_kg.console.rendering import page
from domain_kg.console.workspace import filters_for, read_context
from domain_kg.identity.models import Action
from domain_kg.materials.management import ManagementState
from domain_kg.materials.models import RestoreMaterial
from domain_kg.models import PageRequest
from domain_kg.transport.runtime import ServiceRuntime


def management_router(runtime: ServiceRuntime) -> APIRouter:
    router = APIRouter()

    @router.get("/kgs/{kg_id}/materials/manage")
    async def manage(
        request: Request,
        kg_id: UUID,
        state: ManagementState = "removed",
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> HTMLResponse:
        session, access, scope = await read_context(runtime, request, kg_id, filters_for(False))
        access = await runtime.policy.revalidate(access, action=Action.REPLACE)
        result = await runtime.management.list(
            access, kg_id, state, PageRequest(limit=limit, cursor=cursor)
        )
        return await page(
            runtime,
            session,
            access,
            "material_management.html",
            {
                "title": "Removed materials" if state == "removed" else "Pending materials",
                "kg": await runtime.catalog.get_kg(access, kg_id),
                "managed": result,
                "state": state,
                "limit": limit,
                "command_key": str(uuid4()),
            },
            scope=scope,
        )

    @router.post("/kgs/{kg_id}/materials/{material_id}/restore")
    async def restore(request: Request, kg_id: UUID, material_id: UUID) -> RedirectResponse:
        fields, _, access = await command_context(runtime, request, kg_id, Action.REPLACE)
        if set(fields) != {"expected_revision", "idempotency_key"}:
            raise invalid_form()
        try:
            command = RestoreMaterial(
                kg_id=kg_id,
                material_id=material_id,
                expected_revision=int(fields["expected_revision"]),
                idempotency_key=fields["idempotency_key"],
            )
        except (ValueError, ValidationError):
            raise invalid_form() from None
        result = await runtime.lifecycle.restore(access, command)
        if result.job_id:
            return redirect(f"/admin/kgs/{kg_id}/jobs/{result.job_id}")
        return redirect(f"/admin/kgs/{kg_id}/materials/{material_id}?include_unverified=true")

    return router
