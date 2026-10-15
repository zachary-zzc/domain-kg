"""Catch an unprotected MCP mount and a broken client/server contract."""

import importlib.util

import httpx
import pytest


def test_application_factory_exists() -> None:
    assert importlib.util.find_spec("domain_kg") is not None, "application package is missing"


@pytest.mark.asyncio
async def test_mcp_rejects_unauthenticated_access() -> None:
    from domain_kg.app import create_app
    from domain_kg.settings import Settings

    app = create_app(Settings())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
        assert response.status_code == 401
        assert response.headers["www-authenticate"].startswith("Bearer")
        health = await client.get("/healthz")
        assert health.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_official_sdk_calls_tool_over_mounted_streamable_http() -> None:
    import httpx2
    from mcp import Client
    from mcp.server.auth.provider import AccessToken, TokenVerifier

    from domain_kg.app import create_app
    from domain_kg.settings import Settings

    class TestIssuerVerifier(TokenVerifier):
        async def verify_token(self, token: str) -> AccessToken | None:
            if token != "signed-fixture-token":
                return None
            return AccessToken(
                token=token,
                client_id="smoke-client",
                scopes=["domain_kg:read"],
                resource="http://localhost:8000/mcp",
            )

    app = create_app(
        Settings(public_url="http://localhost:8000"), token_verifier=TestIssuerVerifier()
    )
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://localhost:8000",
            headers={"Authorization": "Bearer signed-fixture-token"},
        ) as http_client:
            from mcp.client.streamable_http import streamable_http_client

            transport = streamable_http_client("http://localhost:8000/mcp", http_client=http_client)
            async with Client(transport) as client:
                tools = await client.list_tools()
                assert [tool.name for tool in tools.tools] == ["domain_kg_probe"]
                result = await client.call_tool("domain_kg_probe", {})
                assert result.structured_content == {"status": "ok"}
                assert client.protocol_version == "2026-07-28"
