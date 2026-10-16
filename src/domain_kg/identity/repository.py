"""Mongo identity authority. Every administrative mutation rechecks its actor.

The organization document serializes policy mutations in a real Mongo transaction,
including changes to different admins, so the last-admin invariant is concurrent.
"""

import hashlib
import json
import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.invariants import assert_active_owners
from domain_kg.identity.models import Group, Member, Principal
from domain_kg.identity.schema import install_identity_schema
from domain_kg.models import FrozenModel, IdempotencyKey, PageRequest


class _Mutation(FrozenModel):
    idempotency_key: IdempotencyKey


class IdentityRepository:
    def __init__(self, database: AsyncDatabase[dict[str, Any]]) -> None:
        self.database = database

    async def initialize(self) -> None:
        await install_identity_schema(self.database)
        await self.database.identity_cursors.create_index("expires_at", expireAfterSeconds=0)
        await self.database.members.create_index(
            [("org_id", 1), ("issuer", 1), ("subject", 1)], unique=True
        )
        await self.database.members.create_index([("issuer", 1), ("subject", 1), ("suspended", 1)])
        await self.database.groups.create_index([("org_id", 1), ("id", 1)], unique=True)
        await self.database.identity_commands.create_index(
            [("org_id", 1), ("actor_id", 1), ("key", 1)], unique=True
        )

    @staticmethod
    def _member(raw: dict[str, Any]) -> Member:
        return Member.model_validate({key: value for key, value in raw.items() if key != "_id"})

    async def bootstrap_organization(self, principal: Principal, name: str) -> tuple[UUID, Member]:
        """Operator provisioning hook, never a login or public HTTP operation."""
        org = uuid4()
        member = Member(
            id=uuid4(), org_id=org, issuer=principal.issuer, subject=principal.subject, role="admin"
        )

        async def create(session: AsyncClientSession) -> None:
            await self.database.organizations.insert_one(
                {"_id": str(org), "name": name, "policy_revision": 0, "active_admins": 1},
                session=session,
            )
            await self.database.members.insert_one(
                member.model_dump(mode="json") | {"_id": str(member.id)}, session=session
            )

        async with self.database.client.start_session() as session:
            await session.with_transaction(create)
        return org, member

    async def require_member(self, principal: Principal, org_id: UUID | None = None) -> Member:
        query = {"issuer": principal.issuer, "subject": principal.subject, "suspended": False}
        if org_id is not None:
            query["org_id"] = str(org_id)
        rows = await self.database.members.find(query).limit(2).to_list()
        if len(rows) != 1:
            raise DomainError(
                ErrorCode.FORBIDDEN, "An active organization membership must be selected."
            )
        return self._member(rows[0])

    async def has_active_read_credential(self, principal: Principal) -> bool:
        """Authenticate a multi-org credential without selecting an organization grant.

        Each service operation still selects one current membership and intersects
        that membership's service scopes before authorizing any KG or material.
        """
        if "domain_kg:read" not in principal.scopes:
            return False
        return await self.database.members.find_one(
            {
                "issuer": principal.issuer,
                "subject": principal.subject,
                "suspended": False,
                "$or": [
                    {"kind": "human"},
                    {"kind": "service", "scopes": "domain_kg:read"},
                ],
            },
            {"_id": 1},
        ) is not None

    async def _admin(self, actor: Member, session: AsyncClientSession) -> Member:
        raw = await self.database.members.find_one(
            {
                "_id": str(actor.id),
                "org_id": str(actor.org_id),
                "issuer": actor.issuer,
                "subject": actor.subject,
                "kind": "human",
                "role": "admin",
                "suspended": False,
            },
            session=session,
        )
        if raw is None:
            raise DomainError(
                ErrorCode.FORBIDDEN, "Current organization administrator access is required."
            )
        return self._member(raw)

    async def _mutate(
        self,
        actor: Member,
        key: str,
        operation: str,
        values: dict[str, Any],
        apply: Callable[[AsyncClientSession], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        key = _Mutation(idempotency_key=key).idempotency_key
        digest = hashlib.sha256(
            json.dumps(
                {"operation": operation, "values": values}, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()

        async def transact(session: AsyncClientSession) -> dict[str, Any]:
            await self._admin(actor, session)
            command_key = {"org_id": str(actor.org_id), "actor_id": str(actor.id), "key": key}
            existing = await self.database.identity_commands.find_one(command_key, session=session)
            if existing is not None:
                if existing["digest"] != digest:
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT,
                        "The idempotency key was used for different input.",
                    )
                result: dict[str, Any] = existing["result"]
                return result
            result = await apply(session)
            await assert_active_owners(self.database, str(actor.org_id), session)
            # This shared write makes concurrent policy transactions conflict and retry.
            await self.database.organizations.update_one(
                {"_id": str(actor.org_id)}, {"$inc": {"policy_revision": 1}}, session=session
            )
            await self.database.identity_commands.insert_one(
                command_key | {"digest": digest, "result": result}, session=session
            )
            await self.database.identity_audit.insert_one(
                {
                    "org_id": str(actor.org_id),
                    "actor_id": str(actor.id),
                    "operation": operation,
                    "target_id": result.get("id"),
                    "at": datetime.now(UTC),
                },
                session=session,
            )
            return result

        async with self.database.client.start_session() as session:
            return await session.with_transaction(transact)

    async def admit(
        self,
        actor: Member,
        principal: Principal,
        *,
        role: Literal["admin", "member"],
        idempotency_key: str,
        kind: Literal["human", "service"] = "human",
        kg_ids: tuple[UUID, ...] = (),
        scopes: tuple[str, ...] = (),
    ) -> Member:
        member = Member(
            id=uuid4(),
            org_id=actor.org_id,
            issuer=principal.issuer,
            subject=principal.subject,
            role=role,
            kind=kind,
            kg_ids=kg_ids,
            scopes=frozenset(scopes),
        )
        if kind == "service" and (role != "member" or not kg_ids or not scopes):
            raise DomainError(
                ErrorCode.VALIDATION_ERROR,
                "Service identities require KG IDs and scopes and cannot be administrators.",
            )
        if kind == "human" and (kg_ids or scopes):
            raise DomainError(
                ErrorCode.VALIDATION_ERROR, "Service restrictions belong to service identities."
            )
        values = member.model_dump(mode="json", exclude={"id"})
        values["scopes"] = sorted(member.scopes)

        async def apply(session: AsyncClientSession) -> dict[str, Any]:
            for kg in kg_ids:
                if (
                    await self.database.kgs.find_one(
                        {"org_id": str(actor.org_id), "id": str(kg)}, session=session
                    )
                    is None
                ):
                    raise inaccessible_reference()
            existing = await self.database.members.find_one(
                {
                    "org_id": str(actor.org_id),
                    "issuer": principal.issuer,
                    "subject": principal.subject,
                },
                session=session,
            )
            if existing is not None:
                raise DomainError(
                    ErrorCode.VERSION_CONFLICT, "The identity already has a membership."
                )
            data = member.model_dump(mode="json")
            await self.database.members.insert_one(data | {"_id": str(member.id)}, session=session)
            if role == "admin":
                await self.database.organizations.update_one(
                    {"_id": str(actor.org_id)}, {"$inc": {"active_admins": 1}}, session=session
                )
            return data

        return Member.model_validate(
            await self._mutate(actor, idempotency_key, "admit", values, apply)
        )

    async def change_member(
        self,
        actor: Member,
        member_id: UUID,
        *,
        expected_revision: int,
        idempotency_key: str,
        role: Literal["admin", "member"] | None = None,
        suspended: bool | None = None,
    ) -> Member:
        values = {
            "id": str(member_id),
            "expected_revision": expected_revision,
            "role": role,
            "suspended": suspended,
        }

        async def apply(session: AsyncClientSession) -> dict[str, Any]:
            raw = await self.database.members.find_one(
                {"_id": str(member_id), "org_id": str(actor.org_id)}, session=session
            )
            if raw is None:
                raise inaccessible_reference()
            member = self._member(raw)
            if member.revision != expected_revision:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The membership revision changed.")
            updated = Member.model_validate(
                member.model_dump()
                | {
                    "role": role or member.role,
                    "suspended": member.suspended if suspended is None else suspended,
                    "revision": member.revision + 1,
                }
            )
            if updated.kind == "service" and updated.role == "admin":
                raise DomainError(
                    ErrorCode.FORBIDDEN, "Service identities cannot be administrators."
                )
            before = member.role == "admin" and not member.suspended
            after = updated.role == "admin" and not updated.suspended
            delta = int(after) - int(before)
            if delta:
                query: dict[str, Any] = {"_id": str(actor.org_id)}
                if delta < 0:
                    query["active_admins"] = {"$gt": 1}
                changed = await self.database.organizations.update_one(
                    query, {"$inc": {"active_admins": delta}}, session=session
                )
                if changed.matched_count != 1:
                    raise DomainError(
                        ErrorCode.FORBIDDEN,
                        "The last active organization administrator must remain.",
                    )
            data = updated.model_dump(mode="json")
            await self.database.members.replace_one(
                {"_id": str(member_id), "org_id": str(actor.org_id), "revision": expected_revision},
                data | {"_id": str(member_id)},
                session=session,
            )
            return data

        return Member.model_validate(
            await self._mutate(actor, idempotency_key, "change_member", values, apply)
        )

    async def create_group(self, actor: Member, name: str, *, idempotency_key: str) -> Group:
        group = Group(id=uuid4(), org_id=actor.org_id, name=name)

        async def apply(session: AsyncClientSession) -> dict[str, Any]:
            data = group.model_dump(mode="json")
            await self.database.groups.insert_one(data | {"_id": str(group.id)}, session=session)
            return data

        return Group.model_validate(
            await self._mutate(actor, idempotency_key, "create_group", {"name": group.name}, apply)
        )

    async def set_group_member(
        self, actor: Member, group_id: UUID, member_id: UUID, *, present: bool, idempotency_key: str
    ) -> Group:
        async def apply(session: AsyncClientSession) -> dict[str, Any]:
            group = await self.database.groups.find_one(
                {"_id": str(group_id), "org_id": str(actor.org_id)}, session=session
            )
            member = await self.database.members.find_one(
                {"_id": str(member_id), "org_id": str(actor.org_id), "kind": "human"},
                session=session,
            )
            if group is None or member is None:
                raise inaccessible_reference()
            update = {"$addToSet" if present else "$pull": {"member_ids": str(member_id)}}
            await self.database.groups.update_one(
                {"_id": str(group_id), "org_id": str(actor.org_id)}, update, session=session
            )
            result = await self.database.groups.find_one(
                {"_id": str(group_id), "org_id": str(actor.org_id)}, session=session
            )
            assert result is not None
            return {key: value for key, value in result.items() if key != "_id"}

        return Group.model_validate(
            await self._mutate(
                actor,
                idempotency_key,
                "set_group_member",
                {"group": str(group_id), "member": str(member_id), "present": present},
                apply,
            )
        )

    async def list_page(
        self, actor: Member, kind: Literal["members", "services", "groups"], page: PageRequest
    ) -> dict[str, Any]:
        page = PageRequest.model_validate(page.model_dump())
        database = self.database
        async with database.client.start_session() as session:
            await self._admin(actor, session)
        org = await database.organizations.find_one({"_id": str(actor.org_id)})
        if org is None:
            raise DomainError(ErrorCode.FORBIDDEN, "Organization access is unavailable.")
        binding = {
            "org_id": str(actor.org_id),
            "actor_id": str(actor.id),
            "kind": kind,
            "limit": page.limit,
            "revision": org["policy_revision"],
        }
        after = ""
        if page.cursor:
            cursor = await database.identity_cursors.find_one(
                {
                    "_id": hashlib.sha256(page.cursor.encode()).hexdigest(),
                    "expires_at": {"$gt": datetime.now(UTC)},
                }
            )
            if cursor is None or any(cursor.get(key) != value for key, value in binding.items()):
                raise DomainError(
                    ErrorCode.CURSOR_EXPIRED, "The list cursor expired or its scope changed."
                )
            after = cursor["after"]
        query = {"org_id": str(actor.org_id), "_id": {"$gt": after}}
        collection = database.groups if kind == "groups" else database.members
        if kind != "groups":
            query["kind"] = "service" if kind == "services" else "human"
        rows = await collection.find(query).sort("_id", 1).limit(page.limit + 1).to_list()
        current = await database.organizations.find_one(
            {"_id": str(actor.org_id), "policy_revision": org["policy_revision"]}
        )
        if current is None:
            raise DomainError(
                ErrorCode.POLICY_CHANGED, "Organization policy changed during the request."
            )
        next_cursor = None
        if len(rows) > page.limit:
            next_cursor = secrets.token_urlsafe(32)
            await database.identity_cursors.insert_one(
                binding
                | {
                    "_id": hashlib.sha256(next_cursor.encode()).hexdigest(),
                    "after": rows[page.limit - 1]["_id"],
                    "expires_at": datetime.now(UTC) + timedelta(minutes=15),
                }
            )
        return {
            "items": [
                {key: value for key, value in row.items() if key != "_id"}
                for row in rows[: page.limit]
            ],
            "next_cursor": next_cursor,
        }
