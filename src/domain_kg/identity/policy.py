"""Current authoritative policy, independent of HTTP, MCP and projection ACL mirrors."""

import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.invariants import assert_active_owners
from domain_kg.identity.models import AccessContext, Action, KGGrant, Member, Principal
from domain_kg.models import IdempotencyKey

Role = Literal["reader", "editor", "owner"]
READ_ACTIONS = {Action.READ, Action.SEARCH, Action.LIST_KGS}
EDIT_ACTIONS = {Action.INGEST, Action.REPLACE, Action.REMOVE, Action.CURATE}
ORG_ACTIONS = {Action.CREATE_KG, Action.MANAGE_ORGANIZATION}
UUID_ADAPTER = TypeAdapter(UUID)
KEY_ADAPTER = TypeAdapter(IdempotencyKey)


def uuid_value(value: object) -> UUID:
    try:
        return UUID_ADAPTER.validate_python(value)
    except ValidationError as exc:
        raise DomainError(ErrorCode.VALIDATION_ERROR, "A valid UUID is required.") from exc


class Policy:
    def __init__(self, database: AsyncDatabase[dict[str, Any]]) -> None:
        self.database = database

    async def initialize(self) -> None:
        await self.database.kg_grants.create_index(
            [("org_id", 1), ("kg_id", 1), ("subject_kind", 1), ("subject_id", 1)], unique=True
        )
        await self.database.material_access.create_index(
            [("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True
        )
        await self.database.policy_commands.create_index(
            [("org_id", 1), ("kg_id", 1), ("actor", 1), ("key", 1)], unique=True
        )
        await self.database.kgs.create_index([("org_id", 1), ("id", 1)], unique=True)

    async def resolve_access(
        self,
        principal: Principal,
        kg_ids: list[UUID],
        action: Action,
        *,
        org_id: UUID | None = None,
        session: AsyncClientSession | None = None,
    ) -> AccessContext:
        try:
            return await self._resolve(
                principal, [uuid_value(value) for value in kg_ids], action, org_id, session
            )
        except PyMongoError as exc:
            raise DomainError(
                ErrorCode.BACKEND_UNAVAILABLE, "Policy authority is unavailable.", retryable=True
            ) from exc

    async def _resolve(
        self,
        principal: Principal,
        kg_ids: list[UUID],
        action: Action,
        org_id: UUID | None,
        session: AsyncClientSession | None,
    ) -> AccessContext:
        if len(kg_ids) != len(set(kg_ids)):
            raise DomainError(ErrorCode.VALIDATION_ERROR, "KG IDs must be distinct.")
        memberships = await self.database.members.find(
            {"issuer": principal.issuer, "subject": principal.subject, "suspended": False},
            session=session,
        ).to_list()
        choices: list[tuple[Member, list[dict[str, Any]]]] = []
        for raw in memberships:
            member = Member.model_validate(
                {key: value for key, value in raw.items() if key != "_id"}
            )
            if org_id is not None and member.org_id != org_id:
                continue
            kgs = await self.database.kgs.find(
                {"org_id": str(member.org_id), "id": {"$in": [str(value) for value in kg_ids]}},
                session=session,
            ).to_list()
            if len(kgs) == len(kg_ids):
                choices.append((member, kgs))
        if len(choices) != 1:
            raise inaccessible_reference()
        member, kgs = choices[0]
        organization = await self.database.organizations.find_one(
            {"_id": str(member.org_id)}, session=session
        )
        if organization is None:
            raise inaccessible_reference()
        scopes = principal.scopes if member.kind == "human" else principal.scopes & member.scopes
        required_scope = (
            "domain_kg:read"
            if action in READ_ACTIONS
            else "domain_kg:admin"
            if action in ORG_ACTIONS or action == Action.REGISTER_BACKEND
            else "domain_kg:write"
        )
        if required_scope not in scopes:
            raise DomainError(
                ErrorCode.FORBIDDEN, "The credential lacks the required action scope."
            )
        operator = await self.database.platform_operators.find_one(
            {"issuer": principal.issuer, "subject": principal.subject, "active": True},
            session=session,
        )
        if action in ORG_ACTIONS and (member.role != "admin" or member.kind != "human"):
            raise DomainError(ErrorCode.FORBIDDEN, "Organization administrator access is required.")
        if action == Action.REGISTER_BACKEND and (operator is None or member.kind != "human"):
            raise DomainError(ErrorCode.FORBIDDEN, "Platform operator access is required.")
        if not kg_ids and action not in ORG_ACTIONS | {Action.REGISTER_BACKEND, Action.LIST_KGS}:
            raise inaccessible_reference()
        groups = (
            await self.database.groups.find(
                {"org_id": str(member.org_id), "member_ids": str(member.id)}, session=session
            ).to_list()
            if member.kind == "human"
            else []
        )
        group_ids = [str(group["id"]) for group in groups]
        grants: list[KGGrant] = []
        rank = {"reader": 1, "editor": 2, "owner": 3}
        for kg in kgs:
            kg_id = UUID(kg["id"])
            if member.kind == "service" and kg_id not in member.kg_ids:
                raise inaccessible_reference()
            if kg["state"] not in ("ready", "read_only") and action in READ_ACTIONS | EDIT_ACTIONS:
                raise DomainError(
                    ErrorCode.FORBIDDEN, "The KG does not permit this action in its current state."
                )
            if kg["state"] == "read_only" and action in EDIT_ACTIONS:
                raise DomainError(ErrorCode.FORBIDDEN, "The KG is read only.")
            entries = await self.database.kg_grants.find(
                {
                    "org_id": str(member.org_id),
                    "kg_id": str(kg_id),
                    "$or": [
                        {"subject_kind": "member", "subject_id": str(member.id)},
                        {"subject_kind": "group", "subject_id": {"$in": group_ids}},
                    ],
                },
                session=session,
            ).to_list()
            roles = [entry["role"] for entry in entries]
            required_rank = 1 if action in READ_ACTIONS else 2 if action in EDIT_ACTIONS else 3
            if not roles or max(rank.get(role, 0) for role in roles) < required_rank:
                raise inaccessible_reference()
            grant = max(roles, key=lambda role: rank.get(role, 0))
            grants.append(KGGrant(kg_id=kg_id, role=grant))
        if (
            await self.database.organizations.find_one(
                {"_id": str(member.org_id), "policy_revision": organization["policy_revision"]},
                session=session,
            )
            is None
        ):
            raise DomainError(ErrorCode.POLICY_CHANGED, "Policy changed during resolution.")
        if (
            await self.database.members.find_one(
                {
                    "org_id": str(member.org_id),
                    "id": str(member.id),
                    "revision": member.revision,
                    "suspended": False,
                },
                session=session,
            )
            is None
        ):
            raise DomainError(
                ErrorCode.POLICY_CHANGED, "Membership changed during policy resolution."
            )
        return AccessContext(
            principal=Principal(
                issuer=principal.issuer, subject=principal.subject, scopes=scopes, kind=member.kind
            ),
            org_id=member.org_id,
            kg_ids=tuple(kg_ids),
            policy_revision=organization["policy_revision"],
            groups=frozenset(UUID(value) for value in group_ids),
            grants=tuple(grants),
            action=action,
            organization_admin=member.role == "admin" and member.kind == "human",
            platform_operator=operator is not None and member.kind == "human",
        )

    async def revalidate(
        self,
        access: AccessContext,
        *,
        action: Action | None = None,
        session: AsyncClientSession | None = None,
    ) -> AccessContext:
        current = await self.resolve_access(
            access.principal,
            list(access.kg_ids),
            action or access.action,
            org_id=access.org_id,
            session=session,
        )
        if current.policy_revision != access.policy_revision:
            raise DomainError(
                ErrorCode.POLICY_CHANGED, "Policy changed; resolve a new access context."
            )
        return current

    async def material_visible(
        self,
        access: AccessContext,
        kg_id: UUID,
        material_id: UUID,
        *,
        include_excluded: bool = False,
        session: AsyncClientSession | None = None,
    ) -> bool:
        raw = await self._material_acl_record(access, kg_id, material_id, session=session)
        return bool(
            raw is not None
            and not raw.get("tombstoned", False)
            and (include_excluded or not raw.get("excluded", False))
        )

    async def _material_acl_record(
        self,
        access: AccessContext,
        kg_id: UUID,
        material_id: UUID,
        *,
        session: AsyncClientSession | None = None,
    ) -> dict[str, Any] | None:
        """Internal current ACL check, also used by narrowly authorized lifecycle commands.

        This does not grant read visibility to excluded/tombstoned content. Serving
        reads must continue to use material_visible and its immediate fences.
        """
        current = await self.revalidate(access, session=session)
        if kg_id not in current.kg_ids:
            return None
        raw = await self.database.material_access.find_one(
            {
                "org_id": str(current.org_id),
                "kg_id": str(kg_id),
                "id": str(uuid_value(material_id)),
            },
            session=session,
        )
        if raw is None:
            return None
        if raw["acl_mode"] == "kg" or any(
            grant.kg_id == kg_id and grant.role == "owner" for grant in current.grants
        ):
            return raw
        member = await self.database.members.find_one(
            {
                "org_id": str(current.org_id),
                "issuer": current.principal.issuer,
                "subject": current.principal.subject,
                "suspended": False,
            },
            session=session,
        )
        allowed = member is not None and (
            member["id"] in raw.get("member_ids", [])
            or bool(
                set(raw.get("group_ids", [])).intersection(str(group) for group in current.groups)
            )
        )
        return raw if allowed else None

    async def _mutation(
        self,
        access: AccessContext,
        kg_id: UUID,
        key: str,
        operation: str,
        values: dict[str, Any],
        apply: Callable[[AsyncClientSession], Awaitable[None]],
    ) -> None:
        key = KEY_ADAPTER.validate_python(key)
        digest = hashlib.sha256(
            json.dumps({"operation": operation, "values": values}, sort_keys=True).encode()
        ).hexdigest()

        async def transact(session: AsyncClientSession) -> None:
            current = await self.resolve_access(
                access.principal,
                [kg_id],
                Action.MANAGE_GRANTS,
                org_id=access.org_id,
                session=session,
            )
            command = {
                "org_id": str(current.org_id),
                "kg_id": str(kg_id),
                "actor": f"{current.principal.issuer}|{current.principal.subject}",
                "key": key,
            }
            previous = await self.database.policy_commands.find_one(command, session=session)
            if previous:
                if previous["digest"] != digest:
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT,
                        "The idempotency key was used for different input.",
                    )
                return
            if current.policy_revision != access.policy_revision:
                raise DomainError(
                    ErrorCode.POLICY_CHANGED, "Resolve current policy before changing grants."
                )
            await apply(session)
            await assert_active_owners(self.database, str(current.org_id), session)
            await self.database.organizations.update_one(
                {"_id": str(current.org_id)}, {"$inc": {"policy_revision": 1}}, session=session
            )
            await self.database.kgs.update_one(
                {"org_id": str(current.org_id), "id": str(kg_id)},
                {"$inc": {"visibility_revision": 1}},
                session=session,
            )
            await self.database.policy_commands.insert_one(
                command | {"digest": digest}, session=session
            )
            await self.database.policy_audit.insert_one(
                command | {"id": str(uuid4()), "operation": operation, "at": datetime.now(UTC)},
                session=session,
            )

        async with self.database.client.start_session() as session:
            await session.with_transaction(transact)

    async def set_grant(
        self,
        access: AccessContext,
        kg_id: UUID,
        subject_kind: Literal["member", "group"],
        subject_id: UUID,
        role: Role | None,
        *,
        idempotency_key: str,
    ) -> None:
        subject_id, kg_id = uuid_value(subject_id), uuid_value(kg_id)
        if subject_kind not in ("member", "group") or role not in (
            "reader",
            "editor",
            "owner",
            None,
        ):
            raise DomainError(ErrorCode.VALIDATION_ERROR, "Invalid grant.")

        async def apply(session: AsyncClientSession) -> None:
            collection = self.database.members if subject_kind == "member" else self.database.groups
            target = await collection.find_one(
                {"org_id": str(access.org_id), "id": str(subject_id)}, session=session
            )
            if target is None or (role == "owner" and target.get("kind") == "service"):
                raise inaccessible_reference()
            query = {
                "org_id": str(access.org_id),
                "kg_id": str(kg_id),
                "subject_kind": subject_kind,
                "subject_id": str(subject_id),
            }
            if role is None:
                await self.database.kg_grants.delete_one(query, session=session)
            else:
                await self.database.kg_grants.update_one(
                    query, {"$set": {"role": role}}, upsert=True, session=session
                )

        await self._mutation(
            access,
            kg_id,
            idempotency_key,
            "set_grant",
            {"subject_kind": subject_kind, "subject_id": str(subject_id), "role": role},
            apply,
        )

    async def set_material_acl(
        self,
        access: AccessContext,
        kg_id: UUID,
        material_id: UUID,
        acl_mode: Literal["kg", "restricted"],
        *,
        member_ids: tuple[UUID, ...],
        group_ids: tuple[UUID, ...],
        idempotency_key: str,
    ) -> None:
        material_id, kg_id = uuid_value(material_id), uuid_value(kg_id)
        if acl_mode not in ("kg", "restricted"):
            raise DomainError(ErrorCode.VALIDATION_ERROR, "Invalid material ACL mode.")

        async def apply(session: AsyncClientSession) -> None:
            for identifiers, collection in (
                (member_ids, self.database.members),
                (group_ids, self.database.groups),
            ):
                for identifier in identifiers:
                    if (
                        await collection.find_one(
                            {"org_id": str(access.org_id), "id": str(uuid_value(identifier))},
                            session=session,
                        )
                        is None
                    ):
                        raise inaccessible_reference()
            query = {"org_id": str(access.org_id), "kg_id": str(kg_id), "id": str(material_id)}
            if await self.database.materials.find_one(query, session=session) is None:
                raise inaccessible_reference()
            await self.database.material_access.update_one(
                query,
                {
                    "$set": {
                        "acl_mode": acl_mode,
                        "member_ids": [str(value) for value in member_ids],
                        "group_ids": [str(value) for value in group_ids],
                    }
                },
                session=session,
            )

        await self._mutation(
            access,
            kg_id,
            idempotency_key,
            "set_material_acl",
            {
                "id": str(material_id),
                "acl_mode": acl_mode,
                "member_ids": sorted(str(value) for value in member_ids),
                "group_ids": sorted(str(value) for value in group_ids),
            },
            apply,
        )
