"""Bound small JSON envelopes before parsing, including chunked requests."""

import httpx
from fastapi import FastAPI, Request

from domain_kg.transport.limits import RequestBodyLimit


async def test_json_body_limit_rejects_declared_and_streamed_oversize_before_handler():
    app = FastAPI()
    calls = []
    app.add_middleware(RequestBodyLimit, max_json_bytes=64)

    @app.post("/api/v1/search")
    async def inspect(request: Request):
        calls.append(True)
        return await request.json()

    async def chunks():
        yield b'{"query":"'
        yield b"x" * 65
        yield b'"}'

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        for body in (b"x" * 65, chunks()):
            response = await client.post("/api/v1/search", content=body)
            assert response.status_code == 413
            assert response.json()["code"] == "QUOTA_EXCEEDED"
            assert response.headers["cache-control"] == "no-store"
        accepted = await client.post("/api/v1/search", json={"query": "tiny"})
    assert accepted.json() == {"query": "tiny"} and calls == [True]
