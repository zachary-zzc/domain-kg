"""HTTP validation errors must never echo private material or internal inputs."""

import httpx
from fastapi.routing import APIRoute

from domain_kg.app import create_app
from domain_kg.materials.models import AddMaterial
from domain_kg.settings import Settings


async def test_http_validation_uses_safe_domain_error_without_rejected_input():
    app = create_app(Settings())

    async def validate(command: AddMaterial) -> dict[str, str]:
        return {"status": "ok"}

    app.router.routes.insert(0, APIRoute("/fixture", validate, methods=["POST"]))
    private = "private material content in malformed request"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
    ) as client:
        response = await client.post("/fixture", json={"kg_id": private, "inline_text": private})
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert private not in response.text
    assert response.json()["request_id"]
    assert response.headers["cache-control"] == "no-store"
