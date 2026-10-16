"""Mongo reservations and command identities on the shared authority replica set."""

from collections.abc import Awaitable, Callable
from typing import TypeVar
from uuid import UUID

from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.errors import DuplicateKeyError

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import AccessContext, Action
from domain_kg.identity.policy import Policy

T = TypeVar("T")


class MaterialRepository:
    def __init__(self, policy: Policy) -> None:
        self.policy, self.database = policy, policy.database

    async def initialize(self) -> None:
        for name in ("uploads", "material_commands"):
            await self.database[name].create_index(
                [("org_id", 1), ("kg_id", 1), ("issuer", 1), ("subject", 1), ("key", 1)],
                unique=True,
            )
        await self.database.uploads.create_index(
            [("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True
        )
        await self.database.uploads.create_index([("state", 1), ("expires_at", 1)])
        await self.database.kg_quotas.create_index([("org_id", 1), ("kg_id", 1)], unique=True)
        await self.database.org_quotas.create_index("org_id", unique=True)
        for name in ("jobs", "outbox"):
            await self.database[name].create_index(
                [("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True
            )

    @staticmethod
    def key(access: AccessContext, kg: UUID, key: str) -> dict[str, str]:
        return {
            "org_id": str(access.org_id),
            "kg_id": str(kg),
            "issuer": access.principal.issuer,
            "subject": access.principal.subject,
            "key": key,
        }

    async def transaction(self, work: Callable[[AsyncClientSession], Awaitable[T]]) -> T:
        # Initial insert races have no pre-existing row to conflict on. Retry the
        # transaction to observe the winning durable command or quota reservation.
        for attempt in range(3):
            try:
                async with self.database.client.start_session() as session:
                    return await session.with_transaction(work)
            except DuplicateKeyError:
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")

    async def fence(
        self, access: AccessContext, action: Action, session: AsyncClientSession
    ) -> AccessContext:
        current = await self.policy.revalidate(access, action=action, session=session)
        # Policy mutations write this same row, serializing revocation against
        # acceptance, including when the material lives in a different logical DB.
        await self.database.organizations.update_one(
            {"_id": str(current.org_id), "policy_revision": current.policy_revision},
            {"$inc": {"material_epoch": 1}},
            session=session,
        )
        return current

    async def quota(
        self,
        access: AccessContext,
        kg: UUID,
        reserved: int,
        committed: int,
        session: AsyncClientSession,
    ) -> None:
        for collection, query, limit in (
            (self.database.org_quotas, {"org_id": str(access.org_id)}, 2 * 1024**3),
            (
                self.database.kg_quotas,
                {"org_id": str(access.org_id), "kg_id": str(kg)},
                512 * 1024**2,
            ),
        ):
            await collection.update_one(
                query,
                {
                    "$setOnInsert": {
                        "reserved_bytes": 0,
                        "committed_bytes": 0,
                        "limit_bytes": limit,
                    }
                },
                upsert=True,
                session=session,
            )
            raw = await collection.find_one(query, session=session)
            if raw is None:
                raise AssertionError("quota reservation missing")
            if (
                raw["reserved_bytes"] + reserved < 0
                or raw["committed_bytes"] + committed < 0
                or raw["reserved_bytes"] + raw["committed_bytes"] + reserved + committed
                > raw["limit_bytes"]
            ):
                raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Original storage quota exceeded.")
            await collection.update_one(
                query,
                {
                    "$inc": {
                        "reserved_bytes": reserved,
                        "committed_bytes": committed,
                    }
                },
                session=session,
            )
