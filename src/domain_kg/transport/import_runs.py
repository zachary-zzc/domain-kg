"""HTTP import manifests use the same current actor and material admission services."""

from uuid import UUID

from fastapi import APIRouter, Query, Request, Response

from domain_kg.identity.models import AccessContext, Action
from domain_kg.materials.import_runs import (
    CreateImportRun,
    ImportRun,
    ImportRunHead,
    ImportRunItem,
    ImportRunSubmission,
)
from domain_kg.transport.runtime import ServiceRuntime


def import_run_router(runtime: ServiceRuntime) -> APIRouter:
    router = APIRouter()

    async def access(
        request: Request,
        response: Response,
        kg: UUID,
        *,
        mutate: bool = False,
        action: Action = Action.READ,
    ) -> AccessContext:
        response.headers["Cache-Control"] = "no-store"
        principal, member = await runtime.identity.actor(request, mutating=mutate)
        return await runtime.access(principal, (kg,), action, member.org_id)

    @router.post("/kgs/{kg_id}/import-runs", status_code=201)
    async def create(
        request: Request, response: Response, kg_id: UUID, command: CreateImportRun
    ) -> ImportRun:
        return await runtime.import_runs.create(
            await access(request, response, kg_id, mutate=True, action=Action.INGEST),
            kg_id,
            command,
        )

    @router.get("/kgs/{kg_id}/import-runs")
    async def recent(
        request: Request,
        response: Response,
        kg_id: UUID,
        limit: int = Query(default=10, ge=1, le=10),
    ) -> tuple[ImportRunHead, ...]:
        return await runtime.import_runs.list(
            await access(request, response, kg_id), kg_id, limit=limit
        )

    @router.get("/kgs/{kg_id}/import-runs/{run_id}")
    async def get(request: Request, response: Response, kg_id: UUID, run_id: UUID) -> ImportRun:
        return await runtime.import_runs.get(await access(request, response, kg_id), kg_id, run_id)

    @router.post("/kgs/{kg_id}/import-runs/{run_id}/items/{index}")
    async def submit(
        request: Request,
        response: Response,
        kg_id: UUID,
        run_id: UUID,
        index: int,
        command: ImportRunSubmission,
    ) -> ImportRunItem:
        action = Action.REPLACE if command.material_id is not None else Action.INGEST
        return await runtime.import_runs.submit(
            await access(request, response, kg_id, mutate=True, action=action),
            kg_id,
            run_id,
            index,
            command,
        )

    @router.post("/kgs/{kg_id}/import-runs/{run_id}/items/{index}/resume")
    async def resume(
        request: Request, response: Response, kg_id: UUID, run_id: UUID, index: int
    ) -> ImportRunItem:
        return await runtime.import_runs.resume(
            await access(request, response, kg_id, mutate=True), kg_id, run_id, index
        )

    return router
