"""Current-user connection control and human-approved directory management."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.bridge.control import IntegrationControl
from domain_kg.identity.bridge.models import (
    ConnectionPage,
    ConnectionView,
    IntegrationPage,
    IntegrationView,
    LinkApproval,
    LinkPage,
    LinkView,
    RevisionCommand,
)
from domain_kg.identity.models import AccessContext, Action, Principal
from domain_kg.identity.repository import IdentityRepository
from domain_kg.materials.service import digest
from domain_kg.models import PageRequest
from domain_kg.storage.transactions import transaction


class BridgeManagement:
    def __init__(self, control: IntegrationControl, identities: IdentityRepository) -> None:
        self.control, self.identities, self.db = control, identities, identities.database

    async def list_page(
        self,
        principal: Principal,
        org: UUID,
        kind: Literal["integrations", "links", "connections"],
        page: PageRequest,
        integration_id: UUID | None = None,
        own: bool = False,
    ) -> IntegrationPage | LinkPage | ConnectionPage:
        member = await self.identities.require_member(principal, org)
        if own and kind != "connections":
            raise inaccessible_reference()
        access = (
            None
            if own
            else await self.control.policy.resolve_access(
                principal, [], Action.MANAGE_ORGANIZATION, org_id=org
            )
        )
        organization = await self.db.organizations.find_one({"_id": str(org)})
        if organization is None:
            raise inaccessible_reference()
        query: dict[str, Any] = {"org_id": str(org)}
        if own:
            query["member_id"] = str(member.id)
        if integration_id:
            integration = await self.control.record(integration_id)
            if integration.org_id != org:
                raise inaccessible_reference()
            query["integration_id"] = str(integration_id)
        binding = {
            "operation": "bridge_directory",
            "org": str(org),
            "member": str(member.id),
            "kind": kind,
            "query": query,
            "limit": page.limit,
            "revision": organization["policy_revision"],
            "delegation": principal.delegation.model_dump(mode="json")
            if principal.delegation
            else None,
        }
        after = ""
        if page.cursor:
            cursor = await self.db.identity_cursors.find_one(
                {
                    "_id": hashlib.sha256(page.cursor.encode()).hexdigest(),
                    "expires_at": {"$gt": datetime.now(UTC)},
                }
            )
            if cursor is None or cursor.get("binding") != binding:
                raise DomainError(
                    ErrorCode.CURSOR_EXPIRED, "The directory cursor expired or changed scope."
                )
            after = cursor["after"]
        collection = self.db[
            {
                "integrations": "identity_integrations",
                "links": "identity_links",
                "connections": "mcp_connections",
            }[kind]
        ]
        rows = (
            await collection.find(query | {"id": {"$gt": after}})
            .sort("id", 1)
            .limit(page.limit + 1)
            .to_list()
        )
        await self.identities.require_member(principal, org)
        if access:
            await self.control.policy.revalidate(access)
        if not await self.db.organizations.find_one(
            {"_id": str(org), "policy_revision": organization["policy_revision"]}
        ):
            raise DomainError(
                ErrorCode.POLICY_CHANGED, "Organization policy changed during the request."
            )
        next_cursor = None
        if len(rows) > page.limit:
            next_cursor = secrets.token_urlsafe(32)
            await self.db.identity_cursors.insert_one(
                {
                    "_id": hashlib.sha256(next_cursor.encode()).hexdigest(),
                    "binding": binding,
                    "after": rows[page.limit - 1]["id"],
                    "expires_at": datetime.now(UTC) + timedelta(minutes=15),
                }
            )
        if kind == "integrations":
            return IntegrationPage(
                items=tuple(
                    IntegrationView.model_validate(
                        {k: row[k] for k in IntegrationView.model_fields}
                    )
                    for row in rows[: page.limit]
                ),
                next_cursor=next_cursor,
            )
        if kind == "links":
            return LinkPage(
                items=tuple(
                    LinkView.model_validate({k: row[k] for k in LinkView.model_fields})
                    for row in rows[: page.limit]
                ),
                next_cursor=next_cursor,
            )
        return ConnectionPage(
            items=tuple(
                ConnectionView.model_validate({k: row.get(k) for k in ConnectionView.model_fields})
                for row in rows[: page.limit]
            ),
            next_cursor=next_cursor,
        )

    async def approve(
        self, access: AccessContext, identifier: UUID, command: LinkApproval
    ) -> LinkView:
        current = await self.control.policy.revalidate(access, action=Action.MANAGE_ORGANIZATION)

        async def apply(session: AsyncClientSession) -> LinkView:
            await self.control.policy.revalidate(current, session=session)
            query = {"org_id": str(current.org_id), "id": str(identifier)}
            raw = await self.db.identity_links.find_one(query, session=session)
            if raw is None:
                raise inaccessible_reference()
            if await self._command(
                current.principal, current.org_id, identifier, "approve", command, session
            ):
                return LinkView.model_validate({k: raw[k] for k in LinkView.model_fields})
            if raw["revision"] != command.expected_revision:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The identity link changed.")
            await self.db.identity_links.update_one(
                query,
                {"$set": {"approved": command.approved}, "$inc": {"revision": 1}},
                session=session,
            )
            if not command.approved:
                await self.db.mcp_connections.update_many(
                    {"org_id": str(current.org_id), "link_id": str(identifier), "revoked": False},
                    {"$set": {"revoked": True}, "$inc": {"revision": 1}},
                    session=session,
                )
            await self.control.bump(current.org_id, session)
            await self.control.audit(
                current.org_id,
                "link_approval_changed",
                identifier,
                current.principal.model_dump(mode="json"),
                session,
            )
            return LinkView.model_validate(
                {
                    k: (
                        command.approved
                        if k == "approved"
                        else raw[k] + 1
                        if k == "revision"
                        else raw[k]
                    )
                    for k in LinkView.model_fields
                }
            )

        return await transaction(self.db, apply)

    async def _command(
        self,
        principal: Principal,
        org: UUID,
        identifier: UUID,
        operation: str,
        command: RevisionCommand,
        session: AsyncClientSession,
    ) -> bool:
        key = {
            "_id": digest(
                {
                    "org": str(org),
                    "target": str(identifier),
                    "op": operation,
                    "actor": principal.model_dump(mode="json"),
                    "key": command.idempotency_key,
                }
            )
        }
        fingerprint = digest(command.model_dump(mode="json"))
        previous = await self.db.identity_bridge_control.find_one(key, session=session)
        if previous:
            if previous["digest"] != fingerprint:
                raise DomainError(
                    ErrorCode.IDEMPOTENCY_CONFLICT, "The request key has different input."
                )
            return True
        await self.db.identity_bridge_control.insert_one(
            key | {"digest": fingerprint}, session=session
        )
        return False

    async def revoke(
        self,
        principal: Principal,
        org: UUID,
        identifier: UUID,
        command: RevisionCommand,
        own: bool = True,
    ) -> ConnectionView:
        member = await self.identities.require_member(principal, org)
        access = (
            None
            if own
            else await self.control.policy.resolve_access(
                principal, [], Action.MANAGE_ORGANIZATION, org_id=org
            )
        )

        async def apply(session: AsyncClientSession) -> ConnectionView:
            if access:
                await self.control.policy.revalidate(access, session=session)
            else:
                # Recheck authenticated identity inside the same serialized policy mutation.
                if principal.delegation:
                    from domain_kg.identity.delegation import connection_for

                    await connection_for(self.db, principal, session)
                from domain_kg.identity.delegation import member_enabled

                if not await member_enabled(
                    self.db, org, member.id, session
                ) or not await self.db.members.find_one(
                    {"org_id": str(org), "id": str(member.id), "suspended": False}, session=session
                ):
                    raise inaccessible_reference()
            query = {"org_id": str(org), "id": str(identifier)}
            if own:
                query["member_id"] = str(member.id)
            raw = await self.db.mcp_connections.find_one(query, session=session)
            if raw is None:
                raise inaccessible_reference()
            if not await self._command(principal, org, identifier, "revoke", command, session):
                if raw["revision"] != command.expected_revision:
                    raise DomainError(ErrorCode.VERSION_CONFLICT, "The Agent connection changed.")
                await self.db.mcp_connections.update_one(
                    query, {"$set": {"revoked": True}, "$inc": {"revision": 1}}, session=session
                )
                raw = raw | {"revoked": True, "revision": raw["revision"] + 1}
                await self.control.bump(org, session)
                await self.control.audit(
                    org,
                    "connection_revoked",
                    identifier,
                    principal.model_dump(mode="json"),
                    session,
                )
            return ConnectionView.model_validate(
                {k: raw.get(k) for k in ConnectionView.model_fields}
            )

        return await transaction(self.db, apply)
