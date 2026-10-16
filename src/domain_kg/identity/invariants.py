"""Organization-serialized owner invariants shared by identity and KG mutations."""

from typing import Any

from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase

from domain_kg.errors import DomainError, ErrorCode


async def assert_active_owners(
    database: AsyncDatabase[dict[str, Any]], org_id: str, session: AsyncClientSession
) -> None:
    async for kg in database.kgs.find(
        {
            "org_id": org_id,
            "state": {"$in": ["provisioning", "ready", "read_only", "archived", "error"]},
        },
        session=session,
    ):
        active = False
        async for grant in database.kg_grants.find(
            {"org_id": org_id, "kg_id": kg["id"], "role": "owner"}, session=session
        ):
            member_ids = [grant["subject_id"]]
            if grant["subject_kind"] == "group":
                group = await database.groups.find_one(
                    {"org_id": org_id, "id": grant["subject_id"]}, session=session
                )
                member_ids = group["member_ids"] if group else []
            if await database.members.find_one(
                {"org_id": org_id, "id": {"$in": member_ids}, "suspended": False, "kind": "human"},
                session=session,
            ):
                active = True
                break
        if not active:
            raise DomainError(ErrorCode.FORBIDDEN, "Every KG must retain an active human owner.")
