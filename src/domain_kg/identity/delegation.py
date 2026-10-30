"""Every domain authorization, including workers, rechecks durable delegation bounds."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.bridge.models import ConnectionRecord, IdentityLink, IntegrationRecord
from domain_kg.identity.models import Action, Principal


async def member_enabled(
    db: AsyncDatabase[dict[str, Any]],
    org: UUID,
    member: UUID,
    session: AsyncClientSession | None = None,
) -> bool:
    state = await db.member_identity_states.find_one(
        {"org_id": str(org), "id": str(member)}, session=session
    )
    if await db.identity_links.find_one(
        {"org_id": str(org), "member_id": str(member), "disabled": True}, session=session
    ):
        return False
    if state is None:
        return True
    async for link in db.identity_links.find(
        {"org_id": str(org), "member_id": str(member), "approved": True, "disabled": False},
        session=session,
    ):
        if await db.identity_integrations.find_one(
            {"org_id": str(org), "id": link["integration_id"], "active": True}, session=session
        ):
            return True
    return False


async def supported_groups(
    db: AsyncDatabase[dict[str, Any]],
    org: UUID,
    member: UUID,
    session: AsyncClientSession | None = None,
) -> set[str]:
    identifiers: set[str] = set()
    async for support in db.identity_group_supports.find(
        {"org_id": str(org), "member_id": str(member)}, session=session
    ):
        link = await db.identity_links.find_one(
            {
                "org_id": str(org),
                "id": support["link_id"],
                "member_id": str(member),
                "integration_id": support["integration_id"],
                "approved": True,
                "disabled": False,
            },
            session=session,
        )
        if link and await db.identity_integrations.find_one(
            {"org_id": str(org), "id": support["integration_id"], "active": True}, session=session
        ):
            identifiers.add(support["group_id"])
    return identifiers


def invalid_connection() -> DomainError:
    return DomainError(
        ErrorCode.UNAUTHENTICATED, "The Agent connection is expired, revoked or unavailable."
    )


async def connection_for(
    db: AsyncDatabase[dict[str, Any]],
    principal: Principal,
    session: AsyncClientSession | None = None,
) -> ConnectionRecord:
    ref = principal.delegation
    if ref is None:
        raise invalid_connection()
    raw = await db.mcp_connections.find_one(
        {"org_id": str(ref.org_id), "id": str(ref.connection_id)}, session=session
    )
    if raw is None:
        raise invalid_connection()
    connection = ConnectionRecord.model_validate({k: v for k, v in raw.items() if k != "_id"})
    if (
        connection.revoked
        or connection.expires_at <= datetime.now(UTC)
        or (connection.issuer, connection.subject) != (principal.issuer, principal.subject)
    ):
        raise invalid_connection()
    ns = {"org_id": str(ref.org_id)}
    raw_integration = await db.identity_integrations.find_one(
        ns | {"id": str(connection.integration_id)}, session=session
    )
    raw_link = await db.identity_links.find_one(
        ns | {"id": str(connection.link_id)}, session=session
    )
    if raw_integration is None or raw_link is None:
        raise invalid_connection()
    integration = IntegrationRecord.model_validate(
        {k: v for k, v in raw_integration.items() if k != "_id"}
    )
    link = IdentityLink.model_validate({k: v for k, v in raw_link.items() if k != "_id"})
    if (
        connection.org_id != ref.org_id
        or link.org_id != ref.org_id
        or integration.org_id != ref.org_id
        or not integration.active
        or link.disabled
        or not link.approved
        or (link.integration_id, link.member_id, link.canonical_issuer, link.canonical_subject)
        != (integration.id, connection.member_id, principal.issuer, principal.subject)
    ):
        raise invalid_connection()
    return connection


async def restrict(
    db: AsyncDatabase[dict[str, Any]],
    principal: Principal,
    kg_ids: list[UUID],
    action: Action,
    org_id: UUID | None,
    session: AsyncClientSession | None,
) -> UUID | None:
    if principal.delegation is None:
        return org_id
    connection = await connection_for(db, principal, session)
    if org_id is not None and org_id != connection.org_id:
        raise invalid_connection()
    raw = await db.identity_integrations.find_one(
        {"org_id": str(connection.org_id), "id": str(connection.integration_id)}, session=session
    )
    if raw is None:
        raise invalid_connection()
    integration = IntegrationRecord.model_validate({k: v for k, v in raw.items() if k != "_id"})
    if (
        action != Action.LIST_KGS
        and action not in set(connection.actions) & set(integration.allowed_actions)
    ) or not set(kg_ids) <= set(connection.kg_ids) & set(integration.allowed_kg_ids):
        raise DomainError(
            ErrorCode.FORBIDDEN, "The Agent connection does not permit this action or KG."
        )
    return connection.org_id
