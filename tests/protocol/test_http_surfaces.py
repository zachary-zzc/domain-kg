"""Thin HTTP surface advertises the same domain commands and catches path mismatches."""

from types import SimpleNamespace
from uuid import uuid4

import httpx
from fastapi import FastAPI

from domain_kg.transport.http import service_router


async def test_http_discovery_includes_search_curation_jobs_and_lifecycle():
    app = FastAPI()
    app.include_router(service_router(SimpleNamespace()))
    paths = app.openapi()["paths"]
    for path in (
        "/api/v1/search",
        "/api/v1/kgs/{kg_id}/provider",
        "/api/v1/kgs/{kg_id}/nodes/{entity_id}",
        "/api/v1/kgs/{kg_id}/subgraph",
        "/api/v1/kgs/{kg_id}/nodes/{entity_id}/curate",
        "/api/v1/kgs/{kg_id}/relations/{assertion_id}",
        "/api/v1/kgs/{kg_id}/revisions",
        "/api/v1/kgs/{kg_id}/revisions/{revision_id}/rollback",
        "/api/v1/kgs/{kg_id}/materials",
        "/api/v1/kgs/{kg_id}/materials/{material_id}",
        "/api/v1/kgs/{kg_id}/materials/{material_id}/remove",
        "/api/v1/kgs/{kg_id}/materials/{material_id}/restore",
        "/api/v1/kgs/{kg_id}/materials/{material_id}/purge",
        "/api/v1/kgs/{kg_id}/jobs/{job_id}",
        "/api/v1/kgs/{kg_id}/jobs/{job_id}/retry",
        "/api/v1/kgs/{kg_id}/jobs/{job_id}/cancel",
        "/api/v1/kgs/{kg_id}/members",
        "/api/v1/kgs/{kg_id}/backups",
        "/api/v1/kgs/{kg_id}/exports",
        "/api/v1/kgs/{kg_id}/backups/jobs/{job_id}",
        "/api/v1/kgs/{kg_id}/backups/{backup_id}/pages/{index}",
    ):
        assert path in paths


async def test_path_body_mismatch_is_rejected_before_any_service_action():
    from fastapi.responses import JSONResponse

    from domain_kg.errors import DomainError

    app = FastAPI()
    app.include_router(service_router(SimpleNamespace()))

    @app.exception_handler(DomainError)
    async def error(request, exc):
        return JSONResponse({"code": exc.code.value}, status_code=422)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        response = await client.post(
            f"/api/v1/kgs/{uuid4()}/subgraph",
            json={
                "kg_id": str(uuid4()),
                "seed_ids": [str(uuid4())],
            },
        )
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"
