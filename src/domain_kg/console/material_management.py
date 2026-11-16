"""Editor metadata and restoration controls never serve withdrawn source evidence."""

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError

from domain_kg.console.material_commands import command_context, invalid_form, redirect
from domain_kg.console.rendering import browser_session
from domain_kg.identity.models import Action
from domain_kg.materials.management import ManagementState
from domain_kg.materials.models import RestoreMaterial
from domain_kg.transport.runtime import ServiceRuntime


def management_router(runtime: ServiceRuntime) -> APIRouter:
    router = APIRouter()

    @router.get("/kgs/{kg_id}/materials/manage")
    async def manage(
        request: Request,
        kg_id: UUID,
        state: ManagementState = "removed",
    ) -> RedirectResponse:
        session = await browser_session(runtime, request)
        await runtime.access(session.principal, (kg_id,), Action.REPLACE, session.member.org_id)
        view = "removed" if state == "removed" else "processing"
        return redirect(f"/admin/kgs/{kg_id}/materials?view={view}")

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
