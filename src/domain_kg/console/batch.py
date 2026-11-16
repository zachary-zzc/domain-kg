"""Same-origin batch forms delegate every mutation to the shared coordinator."""

import json
from typing import Literal
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError

from domain_kg.console.forms import read_form
from domain_kg.console.material_commands import invalid_form, redirect
from domain_kg.console.rendering import browser_session
from domain_kg.identity.models import Action
from domain_kg.models import FrozenModel
from domain_kg.operations.batch import BatchCommand, BatchOperation
from domain_kg.transport.runtime import ServiceRuntime


class BatchDestination(FrozenModel):
    return_view: Literal["published", "processing", "attention", "removed"] | None = None
    import_id: UUID | None = None


def source_destination(
    kg: UUID, batch_id: UUID, operation: BatchOperation, destination: BatchDestination
) -> str:
    view = destination.return_view or (
        "processing"
        if operation in ("retry", "cancel")
        else "removed"
        if operation == "restore"
        else "published"
    )
    query = {"view": view, "batch_id": str(batch_id)}
    if destination.import_id is not None:
        query["import_id"] = str(destination.import_id)
    return f"/admin/kgs/{kg}/materials?{urlencode(query)}"


def batch_router(runtime: ServiceRuntime) -> APIRouter:
    router = APIRouter()

    @router.post("/kgs/{kg_id}/batch")
    async def submit(request: Request, kg_id: UUID) -> RedirectResponse:
        fields = await read_form(request)
        session = await browser_session(runtime, request, csrf=fields.pop("csrf_token", ""))
        destination_fields = {
            key: fields.pop(key) for key in ("return_view", "import_id") if key in fields
        }
        if set(fields) != {"operation", "items", "idempotency_key"}:
            raise invalid_form()
        try:
            destination = BatchDestination.model_validate(destination_fields)
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
        return redirect(source_destination(kg_id, result.batch_id, result.operation, destination))

    @router.get("/kgs/{kg_id}/batch/{batch_id}")
    async def result(request: Request, kg_id: UUID, batch_id: UUID) -> RedirectResponse:
        session = await browser_session(runtime, request)
        access = await runtime.access(
            session.principal, (kg_id,), Action.READ, session.member.org_id
        )
        batch = await runtime.batch.get(access, kg_id, batch_id)
        return redirect(
            source_destination(kg_id, batch.batch_id, batch.operation, BatchDestination())
        )

    return router
