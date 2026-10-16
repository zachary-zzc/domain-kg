"""Protected resource metadata, challenge location, and session CSRF boundary."""

import httpx


async def test_protected_resource_discovery_is_public_and_bound_to_mcp() -> None:
    from domain_kg.app import create_app
    from domain_kg.settings import Settings

    app = create_app(
        Settings(public_url="http://localhost:8000", oidc_issuer="https://issuer.example")
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
    ) as client:
        denied = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert denied.status_code == 401
        assert (
            'resource_metadata="http://localhost:8000/.well-known/oauth-protected-resource/mcp"'
            in denied.headers["www-authenticate"]
        )
        metadata = await client.get("/.well-known/oauth-protected-resource/mcp")
        assert metadata.status_code == 200
        assert metadata.json()["resource"] == "http://localhost:8000/mcp"
        assert metadata.json()["authorization_servers"] == ["https://issuer.example"]
