"""Owner grant management with current authority, revision CAS and scoped cursors."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.grant_models import GrantPage, GrantSummary, MemberRoleChange, PolicyReceipt
from domain_kg.identity.models import AccessContext, Action
from domain_kg.identity.policy import Policy
from domain_kg.models import PageRequest


class GrantManagement:
    def __init__(self, policy: Policy) -> None:
        self.policy, self.database = policy, policy.database

    async def list(self, access: AccessContext, kg: UUID, page: PageRequest) -> GrantPage:
        current = await self.policy.revalidate(access, action=Action.MANAGE_GRANTS)
        if kg not in current.kg_ids:
            raise inaccessible_reference()
        binding = {
            "org_id": str(current.org_id),
            "kg_id": str(kg),
            "issuer": current.principal.issuer,
            "subject": current.principal.subject,
            "policy_revision": current.policy_revision,
            "limit": page.limit,
        }
        after = ""
        after_kind = ""
        if page.cursor:
            cursor = await self.database.grant_read_cursors.find_one(
                {
                    "_id": hashlib.sha256(page.cursor.encode()).hexdigest(),
                    "expires_at": {"$gt": datetime.now(UTC)},
                }
            )
            if cursor is None or any(cursor.get(key) != value for key, value in binding.items()):
                raise DomainError(ErrorCode.CURSOR_EXPIRED, "The grant cursor scope changed.")
            after = cursor["after"]
            after_kind = cursor["after_kind"]
        rows = (
            await self.database.kg_grants.find(
                {
                    "org_id": str(current.org_id),
                    "kg_id": str(kg),
                    "$or": [
                        {"subject_id": {"$gt": after}},
                        {"subject_id": after, "subject_kind": {"$gt": after_kind}},
                    ],
                }
            )
            .sort([("subject_id", 1), ("subject_kind", 1)])
            .limit(page.limit + 1)
            .to_list()
        )
        items = []
        for row in rows[: page.limit]:
            item = GrantSummary.model_validate(
                {field: row[field] for field in GrantSummary.model_fields}
            )
            collection = (
                self.database.members if item.subject_kind == "member" else self.database.groups
            )
            if (
                await collection.find_one(
                    {"org_id": str(current.org_id), "id": str(item.subject_id)}
                )
                is None
            ):
                raise inaccessible_reference()
            items.append(item)
        await self.policy.revalidate(current, action=Action.MANAGE_GRANTS)
        token = None
        if len(rows) > page.limit:
            token = secrets.token_urlsafe(32)
            await self.database.grant_read_cursors.create_index("expires_at", expireAfterSeconds=0)
            await self.database.grant_read_cursors.insert_one(
                binding
                | {
                    "_id": hashlib.sha256(token.encode()).hexdigest(),
                    "after": rows[page.limit - 1]["subject_id"],
                    "after_kind": rows[page.limit - 1]["subject_kind"],
                    "expires_at": datetime.now(UTC) + timedelta(minutes=15),
                }
            )
        await self.policy.revalidate(current, action=Action.MANAGE_GRANTS)
        return GrantPage(
            items=tuple(items), policy_revision=current.policy_revision, next_cursor=token
        )

    async def set(self, access: AccessContext, command: MemberRoleChange) -> PolicyReceipt:
        command = MemberRoleChange.model_validate(command)
        current = await self.policy.revalidate(access, action=Action.MANAGE_GRANTS)
        values = command.model_dump(mode="json", exclude={"kg_id", "idempotency_key"})

        async def change(session: AsyncClientSession) -> None:
            if current.policy_revision != command.expected_revision:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The grant policy revision changed.")
            collection = (
                self.database.members if command.subject_kind == "member" else self.database.groups
            )
            target = await collection.find_one(
                {
                    "org_id": str(current.org_id),
                    "id": str(command.subject_id),
                },
                session=session,
            )
            if target is None or command.role == "owner" and target.get("kind") == "service":
                raise inaccessible_reference()
            key: dict[str, Any] = {
                "org_id": str(current.org_id),
                "kg_id": str(command.kg_id),
                "subject_kind": command.subject_kind,
                "subject_id": str(command.subject_id),
            }
            if command.role is None:
                await self.database.kg_grants.delete_one(key, session=session)
            else:
                await self.database.kg_grants.update_one(
                    key, {"$set": {"role": command.role}}, upsert=True, session=session
                )

        await self.policy._mutation(
            current, command.kg_id, command.idempotency_key, "set_member_role", values, change
        )
        await self.policy.resolve_access(
            current.principal, [command.kg_id], Action.MANAGE_GRANTS, org_id=current.org_id
        )
        return PolicyReceipt(policy_revision=command.expected_revision + 1)
