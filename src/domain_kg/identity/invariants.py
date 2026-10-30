"""Organization-serialized owner invariants shared by identity and KG mutations."""

from typing import Any
from uuid import UUID

from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.delegation import member_enabled, supported_groups


async def assert_active_admin(
    database: AsyncDatabase[dict[str, Any]], org_id: str, session: AsyncClientSession
) -> None:
    async for member in database.members.find(
        {"org_id": org_id, "role": "admin", "suspended": False, "kind": "human"},
        session=session,
    ):
        if await member_enabled(database, UUID(org_id), UUID(member["id"]), session):
            return
    raise DomainError(
        ErrorCode.FORBIDDEN, "The last active organization administrator must remain."
    )


async def assert_active_owners(
    database: AsyncDatabase[dict[str, Any]], org_id: str, session: AsyncClientSession
) -> None:
    async for kg in database.kgs.find(
        {
            "org_id": org_id,
            "state": {
                "$in": ["provisioning", "ready", "read_only", "archived", "error", "deleted"]
            },
        },
        session=session,
    ):
        active = False
        async for grant in database.kg_grants.find(
            {"org_id": org_id, "kg_id": kg["id"], "role": "owner"}, session=session
        ):
            member_ids = {grant["subject_id"]}
            manual_members: set[str] = set()
            if grant["subject_kind"] == "group":
                group = await database.groups.find_one(
                    {"org_id": org_id, "id": grant["subject_id"]}, session=session
                )
                if group is None:
                    continue
                manual_members = set(group["member_ids"])
                member_ids = manual_members.copy()
                async for support in database.identity_group_supports.find(
                    {"org_id": org_id, "group_id": grant["subject_id"]}, session=session
                ):
                    member_ids.add(support["member_id"])
            async for member in database.members.find(
                {
                    "org_id": org_id,
                    "id": {"$in": list(member_ids)},
                    "suspended": False,
                    "kind": "human",
                },
                session=session,
            ):
                member_id = UUID(member["id"])
                if not await member_enabled(database, UUID(org_id), member_id, session):
                    continue
                if (
                    grant["subject_kind"] == "group"
                    and member["id"] not in manual_members
                    and grant["subject_id"]
                    not in await supported_groups(database, UUID(org_id), member_id, session)
                ):
                    continue
                active = True
                break
            if active:
                break
        if not active:
            raise DomainError(ErrorCode.FORBIDDEN, "Every KG must retain an active human owner.")
