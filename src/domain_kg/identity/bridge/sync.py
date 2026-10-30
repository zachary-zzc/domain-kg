"""Ordered, integration-bound account events; group supports retain independent ownership."""

from typing import TYPE_CHECKING
from uuid import UUID

from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.bridge.models import EventResult, SyncEvent
from domain_kg.identity.delegation import member_enabled
from domain_kg.materials.service import digest
from domain_kg.storage.transactions import transaction

if TYPE_CHECKING:
    from domain_kg.identity.bridge.service import IdentityBridge


async def synchronize(bridge: "IdentityBridge", token: str, command: SyncEvent) -> EventResult:
    integration = await bridge.caller(command.integration_id, token, "identity:sync")
    fingerprint = digest(command.model_dump(mode="json"))
    key = {"_id": digest({"integration": str(integration.id), "event": command.event_id})}

    async def apply(session: AsyncClientSession) -> EventResult:
        if not await bridge.db.identity_integrations.find_one(
            {
                "org_id": str(integration.org_id),
                "id": str(integration.id),
                "active": True,
                "revision": integration.revision,
            },
            session=session,
        ):
            raise DomainError(ErrorCode.POLICY_CHANGED, "The identity integration changed.")
        previous = await bridge.db.identity_sync_events.find_one(key, session=session)
        if previous:
            if previous["digest"] != fingerprint:
                raise DomainError(
                    ErrorCode.IDEMPOTENCY_CONFLICT, "The event ID has different input."
                )
            return EventResult.model_validate(previous["result"])
        link = await bridge.link(integration, command.external_subject, session)
        if command.source_version <= link.source_version:
            result = EventResult(
                event_id=command.event_id, applied=False, source_version=link.source_version
            )
        else:
            query = {"org_id": str(integration.org_id), "id": str(link.id)}
            updates: dict[str, object] = {"source_version": command.source_version}
            if command.display_name is not None:
                updates["display_name"] = command.display_name
            if command.operation in ("disable", "enable"):
                updates["disabled"] = command.operation == "disable"
            await bridge.db.identity_links.update_one(
                query, {"$set": updates, "$inc": {"revision": 1}}, session=session
            )
            if command.operation == "disable":
                await bridge.db.mcp_connections.update_many(
                    {
                        "org_id": str(integration.org_id),
                        "member_id": str(link.member_id),
                        "revoked": False,
                    },
                    {"$set": {"revoked": True}, "$inc": {"revision": 1}},
                    session=session,
                )
            if command.groups is not None:
                mapping = {m.external_group: m.group_id for m in integration.group_mappings}
                # Unknown upstream groups never grant authority. Replace only this link's supports.
                selected = {mapping[g] for g in command.groups if g in mapping}
                for group_id in selected:
                    if not await bridge.db.groups.find_one(
                        {"org_id": str(integration.org_id), "id": str(group_id)}, session=session
                    ):
                        raise inaccessible_reference()
                await bridge.db.identity_group_supports.delete_many(
                    {"org_id": str(integration.org_id), "link_id": str(link.id)}, session=session
                )
                for group_id in selected:
                    await bridge.db.identity_group_supports.insert_one(
                        {
                            "_id": f"{link.id}:{group_id}",
                            "org_id": str(integration.org_id),
                            "link_id": str(link.id),
                            "integration_id": str(integration.id),
                            "member_id": str(link.member_id),
                            "group_id": str(group_id),
                        },
                        session=session,
                    )
            recovery_required = False
            if command.operation == "disable":
                recovery_required = True
                async for member in bridge.db.members.find(
                    {
                        "org_id": str(integration.org_id),
                        "kind": "human",
                        "role": "admin",
                        "suspended": False,
                    },
                    session=session,
                ):
                    if await member_enabled(
                        bridge.db, integration.org_id, UUID(member["id"]), session
                    ):
                        recovery_required = False
                        break
            result = EventResult(
                event_id=command.event_id,
                applied=True,
                source_version=command.source_version,
                recovery_required=recovery_required,
            )
            await bridge.control.bump(integration.org_id, session)
            await bridge.control.audit(
                integration.org_id,
                "identity_" + command.operation,
                link.member_id,
                {"integration_id": str(integration.id), "event_id": command.event_id},
                session,
            )
        await bridge.db.identity_sync_events.insert_one(
            key
            | {
                "org_id": str(integration.org_id),
                "integration_id": str(integration.id),
                "digest": fingerprint,
                "result": result.model_dump(mode="json"),
            },
            session=session,
        )
        return result

    return await transaction(bridge.db, apply)
