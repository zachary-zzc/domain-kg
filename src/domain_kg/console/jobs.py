"""Current-session job inspection and durable controls, without worker shortcuts."""

from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import TypeAdapter, ValidationError

from domain_kg.console.forms import read_form
from domain_kg.console.rendering import browser_session, page
from domain_kg.console.workspace import filters_for, read_context
from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.models import IdempotencyKey, PageRequest
from domain_kg.operations.restore_results import RestoreResults
from domain_kg.transport.runtime import ServiceRuntime


def job_router(runtime: ServiceRuntime) -> APIRouter:
    router = APIRouter()

    @router.get("/kgs/{kg_id}/jobs")
    async def jobs(
        request: Request,
        kg_id: UUID,
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> HTMLResponse:
        session, access, scope = await read_context(runtime, request, kg_id, filters_for(False))
        result = await runtime.job_inspection.list(
            access, kg_id, PageRequest(limit=limit, cursor=cursor)
        )
        return await page(
            runtime,
            session,
            access,
            "jobs.html",
            {
                "title": "Processing jobs",
                "kg": await runtime.catalog.get_kg(access, kg_id),
                "jobs": result,
                "limit": limit,
            },
            scope=scope,
        )

    @router.get("/kgs/{kg_id}/jobs/{job_id}")
    async def job(request: Request, kg_id: UUID, job_id: UUID) -> HTMLResponse:
        session, access, scope = await read_context(runtime, request, kg_id, filters_for(False))
        result = await runtime.queue.get(access, job_id)
        backup_url = None
        export_url = None
        restore_url = None
        restore_info = None
        if any(grant.kg_id == kg_id and grant.role == "owner" for grant in access.grants):
            backup = await runtime.queue.database.jobs.find_one(
                {
                    "org_id": str(access.org_id),
                    "kg_id": str(kg_id),
                    "id": str(job_id),
                    "kind": {"$in": ["backup", "export", "restore"]},
                },
                {"_id": 0, "id": 1, "kind": 1},
            )
            if backup and backup["kind"] == "backup":
                backup_url = f"/admin/kgs/{kg_id}/backups/jobs/{job_id}"
            elif backup and backup["kind"] == "export":
                export_url = f"/admin/kgs/{kg_id}/exports/jobs/{job_id}"
            elif backup and backup["kind"] == "restore":
                restore_info = await runtime.restores.get(access, kg_id, job_id)
                if result.stage == "ready":
                    restored = await RestoreResults(runtime.queue).get(access, kg_id, job_id)
                    restore_url = f"/admin/kgs/{restored.target.kg_id}/search"
        return await page(
            runtime,
            session,
            access,
            "job.html",
            {
                "title": "Processing status",
                "kg": await runtime.catalog.get_kg(access, kg_id),
                "job": result,
                "backup_url": backup_url,
                "export_url": export_url,
                "restore_url": restore_url,
                "restore_info": restore_info,
                "command_key": str(uuid4()),
            },
            scope=scope,
        )

    @router.post("/kgs/{kg_id}/jobs/{job_id}/{operation}")
    async def control(
        request: Request,
        kg_id: UUID,
        job_id: UUID,
        operation: Literal["cancel", "retry"],
    ) -> RedirectResponse:
        fields = await read_form(request)
        session = await browser_session(runtime, request, csrf=fields.pop("csrf_token", ""))
        access = await runtime.access(
            session.principal, (kg_id,), Action.INGEST, session.member.org_id
        )
        if set(fields) != {"idempotency_key"}:
            raise DomainError(ErrorCode.VALIDATION_ERROR, "The form fields are invalid.")
        try:
            key = TypeAdapter(IdempotencyKey).validate_python(fields["idempotency_key"])
        except ValidationError:
            raise DomainError(ErrorCode.VALIDATION_ERROR, "The form fields are invalid.") from None
        if operation == "cancel":
            await runtime.queue.cancel(access, job_id, idempotency_key=key)
        else:
            await runtime.queue.retry(access, job_id, idempotency_key=key)
        return RedirectResponse(
            f"/admin/kgs/{kg_id}/jobs/{job_id}",
            status_code=303,
            headers={"Cache-Control": "no-store"},
        )

    return router
