"""Protected HTTP and MCP surfaces over the same application services."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl
from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError

from domain_kg.catalog.backends import BackendRegistry, approved_from_settings
from domain_kg.catalog.routes import catalog_router
from domain_kg.catalog.service import CatalogService
from domain_kg.console.rendering import ROOT as CONSOLE_ROOT
from domain_kg.console.rendering import error_page
from domain_kg.console.routes import console_router
from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.policy import Policy
from domain_kg.identity.repository import IdentityRepository
from domain_kg.identity.routes import IdentityRuntime, identity_router
from domain_kg.materials.routes import material_router
from domain_kg.materials.service import MaterialService
from domain_kg.providers.profiles import ProviderRegistry
from domain_kg.providers.service import ProviderService
from domain_kg.settings import Settings
from domain_kg.storage.router import KGStoreRouter
from domain_kg.transport.errors import SafeMCPServer
from domain_kg.transport.http import service_router
from domain_kg.transport.limits import RequestBodyLimit
from domain_kg.transport.mcp import register_tools
from domain_kg.transport.resources import register_resources
from domain_kg.transport.runtime import ServiceRuntime


class DenyAllVerifier(TokenVerifier):
    """Fail closed until the external OIDC verifier is configured in Task 3."""

    async def verify_token(self, token: str) -> AccessToken | None:
        return None


def create_app(
    settings: Settings,
    *,
    token_verifier: TokenVerifier | None = None,
    identity_repository: IdentityRepository | None = None,
    oidc_http_client: httpx.AsyncClient | None = None,
    catalog_service: CatalogService | None = None,
    providers: ProviderService | None = None,
) -> FastAPI:
    mongo_client: AsyncMongoClient[dict[str, Any]] | None = None
    http_client = None
    identity = None
    catalog = catalog_service
    materials = None
    owns_catalog = False
    services = None
    if settings.oidc_client_id:
        repository = identity_repository
        if repository is None:
            mongo_client = AsyncMongoClient(
                settings.mongo_uri.get_secret_value(), serverSelectionTimeoutMS=5000
            )
            repository = IdentityRepository(mongo_client[settings.mongo_database])
        client = oidc_http_client
        if client is None:
            http_client = httpx.AsyncClient(timeout=5, follow_redirects=False, trust_env=False)
            client = http_client
        identity = IdentityRuntime(settings, repository, client)
        if catalog is None:
            policy = Policy(repository.database)
            registry = BackendRegistry(policy, approved_from_settings(settings))
            catalog = CatalogService(
                policy,
                registry,
                KGStoreRouter(policy, registry, dimensions=settings.embedding_dimensions),
            )
            owns_catalog = True
        materials = MaterialService(catalog.policy, catalog.router)
        providers = providers or ProviderService(
            catalog.policy, catalog.router, ProviderRegistry.from_file(settings.provider_config)
        )
        from domain_kg.operations.restore_cleanup_policy import RestoreCleanupPolicy
        from domain_kg.operations.retention_policy import RetentionPolicy

        services = ServiceRuntime(
            identity,
            catalog,
            materials,
            providers,
            retention_policy=RetentionPolicy.from_file(settings.retention_config),
            cleanup_policy=RestoreCleanupPolicy.from_file(settings.restore_cleanup_config),
        )
    server: MCPServer[None] = SafeMCPServer(
        "domain_kg_mcp",
        version="0.1.0",
        token_verifier=token_verifier or identity or DenyAllVerifier(),
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(settings.oidc_issuer),
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

    if services is not None:
        register_tools(server, services)
        register_resources(server, services)

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
        try:
            if identity is not None:
                await identity.initialize()
            if catalog is not None:
                await catalog.policy.initialize()
                await catalog.registry.initialize()
                await catalog.initialize()
            if materials is not None:
                await materials.initialize()
            if services is not None:
                await services.initialize()
            async with server.session_manager.run():
                yield
        finally:
            if catalog is not None and owns_catalog:
                await catalog.router.close()
            if mongo_client is not None:
                await mongo_client.close()
            if http_client is not None:
                await http_client.aclose()

    app = FastAPI(title="Domain KG", lifespan=lifespan)
    app.add_middleware(RequestBodyLimit)
    app.state.identity = identity
    app.state.catalog = catalog
    app.state.materials = materials
    app.state.services = services

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, error: DomainError) -> Response:
        status = {
            ErrorCode.UNAUTHENTICATED: 401,
            ErrorCode.FORBIDDEN: 403,
            ErrorCode.RESOURCE_NOT_ACCESSIBLE: 404,
            ErrorCode.VALIDATION_ERROR: 422,
            ErrorCode.UNSUPPORTED_FILTER: 422,
            ErrorCode.VERSION_CONFLICT: 409,
            ErrorCode.IDEMPOTENCY_CONFLICT: 409,
            ErrorCode.CURSOR_EXPIRED: 409,
            ErrorCode.POLICY_CHANGED: 409,
            ErrorCode.QUOTA_EXCEEDED: 429,
            ErrorCode.REVISION_NOT_READY: 409,
            ErrorCode.UNSUPPORTED_FORMAT: 415,
            ErrorCode.EVIDENCE_VALIDATION_FAILED: 422,
        }.get(error.code, 503)
        if request.url.path == "/admin" or request.url.path.startswith("/admin/"):
            return error_page(error, status)
        headers = {"Cache-Control": "no-store"}
        if status == 401:
            headers["WWW-Authenticate"] = (
                f'Bearer resource_metadata="{public_url}/.well-known/oauth-protected-resource/mcp"'
            )
        return JSONResponse(
            {
                "code": error.code,
                "message": error.message,
                "retryable": error.retryable,
                "request_id": str(error.request_id),
            },
            status_code=status,
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> Response:
        return await domain_error_handler(
            request, DomainError(ErrorCode.VALIDATION_ERROR, "The request fields are invalid.")
        )

    @app.exception_handler(PyMongoError)
    async def authority_error(request: Request, error: PyMongoError) -> Response:
        return await domain_error_handler(
            request,
            DomainError(
                ErrorCode.BACKEND_UNAVAILABLE, "Identity authority is unavailable.", retryable=True
            ),
        )

    @app.exception_handler(httpx.HTTPError)
    async def provider_error(request: Request, error: httpx.HTTPError) -> Response:
        return await domain_error_handler(
            request,
            DomainError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "The identity provider is unavailable.",
                retryable=True,
            ),
        )

    if identity is not None:
        app.include_router(identity_router(identity))
        if catalog is not None:
            app.include_router(catalog_router(identity, catalog))
        if materials is not None:
            app.include_router(material_router(identity, materials))
        if services is not None:
            app.include_router(service_router(services))
            app.include_router(console_router(services))
            app.mount(
                "/admin/static",
                StaticFiles(directory=CONSOLE_ROOT / "static"),
                name="console-static",
            )

    @app.middleware("http")
    async def private_identity_responses(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        if request.url.path.startswith(("/api/v1/", "/auth/", "/admin")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/.well-known/oauth-protected-resource/mcp")
    async def protected_resource() -> dict[str, Any]:
        # Keep the exact issuer identifier; normalizing it can change iss.
        return {
            "resource": f"{public_url}/mcp",
            "authorization_servers": [settings.oidc_issuer],
            "scopes_supported": ["domain_kg:read"],
            "bearer_methods_supported": ["header"],
        }

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # Mount at root: SDK owns /mcp and its resource discovery routes.
    app.mount("/", mcp_app)
    return app
