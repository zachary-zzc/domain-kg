"""Compatibility probe: protected MCP mount, with no material endpoints yet."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl

from domain_kg.settings import Settings


class DenyAllVerifier(TokenVerifier):
    """Fail closed until the external OIDC verifier is configured in Task 3."""

    async def verify_token(self, token: str) -> AccessToken | None:
        return None


def create_app(settings: Settings, *, token_verifier: TokenVerifier | None = None) -> FastAPI:
    server: MCPServer[None] = MCPServer(
        "domain_kg_mcp",
        version="0.1.0",
        token_verifier=token_verifier or DenyAllVerifier(),
        auth=AuthSettings(
            issuer_url=settings.oidc_issuer,
            resource_server_url=AnyHttpUrl(f"{str(settings.public_url).rstrip('/')}/mcp"),
            required_scopes=["domain_kg:read"],
            validate_token_resource=True,
        ),
    )

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
        )
    )
    async def domain_kg_probe() -> dict[str, str]:
        """Check the authenticated MCP transport without accessing any KG data."""
        return {"status": "ok"}

    public_url = str(settings.public_url).rstrip("/")
    mcp_app = server.streamable_http_app(
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[urlsplit(public_url).netloc],
            allowed_origins=[public_url],
        ),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with server.session_manager.run():
            yield

    app = FastAPI(title="Domain KG", lifespan=lifespan)

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # Mount at root: SDK owns /mcp and its resource discovery routes.
    app.mount("/", mcp_app)
    return app
