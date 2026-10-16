"""MCP credential context establishes the caller; arguments only select a namespace."""

from uuid import UUID

from mcp.server.auth.middleware.auth_context import get_access_token

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import AccessContext, Action, Principal
from domain_kg.transport.runtime import ServiceRuntime


async def verified_access(
    runtime: ServiceRuntime | None,
    kg_ids: tuple[UUID, ...],
    action: Action,
    org_id: UUID | None,
) -> AccessContext:
    token = get_access_token()
    if token is None or runtime is None or not token.subject or not token.claims:
        raise DomainError(
            ErrorCode.UNAUTHENTICATED, "A verified HTTP bearer credential is required."
        )
    issuer = token.claims.get("iss")
    if issuer != runtime.identity.settings.oidc_issuer or token.claims.get("sub") != token.subject:
        raise DomainError(ErrorCode.UNAUTHENTICATED, "The verified credential is not valid here.")
    principal = Principal(issuer=issuer, subject=token.subject, scopes=frozenset(token.scopes))
    return await runtime.access(principal, kg_ids, action, org_id)
