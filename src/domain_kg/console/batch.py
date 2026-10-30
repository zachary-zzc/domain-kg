"""Same-origin batch forms delegate every mutation to the shared coordinator."""

import json
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from domain_kg.console.forms import read_form
from domain_kg.console.material_commands import invalid_form, redirect
from domain_kg.console.rendering import browser_session, page
from domain_kg.identity.models import Action
from domain_kg.operations.batch import BatchCommand
from domain_kg.transport.runtime import ServiceRuntime


def batch_router(runtime: ServiceRuntime) -> APIRouter:
    router = APIRouter()

    @router.post("/kgs/{kg_id}/batch")
    async def submit(request: Request, kg_id: UUID) -> RedirectResponse:
        fields = await read_form(request)
        session = await browser_session(runtime, request, csrf=fields.pop("csrf_token", ""))
        if set(fields) != {"operation", "items", "idempotency_key"}:
            raise invalid_form()
        try:
            command = BatchCommand.model_validate(fields | {"items": json.loads(fields["items"])})
        except (ValueError, ValidationError):
            raise invalid_form() from None
        access = await runtime.access(
            session.principal,
            (kg_id,),
            runtime.batch.action(command.operation),
            session.member.org_id,
        )
        result = await runtime.batch.execute(access, kg_id, command)
        return redirect(f"/admin/kgs/{kg_id}/batch/{result.batch_id}")

    @router.get("/kgs/{kg_id}/batch/{batch_id}")
    async def result(request: Request, kg_id: UUID, batch_id: UUID) -> HTMLResponse:
        session = await browser_session(runtime, request)
        access = await runtime.access(
            session.principal, (kg_id,), Action.READ, session.member.org_id
        )
        batch = await runtime.batch.get(access, kg_id, batch_id)
        return await page(
            runtime,
            session,
            access,
            "batch_result.html",
            {
                "title": "Batch results",
                "kg": await runtime.catalog.get_kg(access, kg_id),
                "batch": batch,
                "counts": {
                    outcome: sum(item.outcome == outcome for item in batch.items)
                    for outcome in ("submitted", "completed", "skipped", "failed", "pending")
                },
            },
        )

    return router
