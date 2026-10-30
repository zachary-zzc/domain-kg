"""Transactional catalog reservations, explicit ownership and revision-aware state."""

from typing import Any
from uuid import UUID, uuid4

from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.catalog.backends import BackendRegistry
from domain_kg.catalog.models import CreateKG
from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.invariants import assert_active_owners
from domain_kg.identity.models import AccessContext, Action
from domain_kg.identity.policy import Policy


class CatalogRepository:
    def __init__(self, policy: Policy, registry: BackendRegistry) -> None:
        self.policy, self.registry, self.database = policy, registry, policy.database

    async def initialize(self) -> None:
        await self.database.catalog_commands.create_index(
            [("org_id", 1), ("issuer", 1), ("subject", 1), ("key", 1)], unique=True
        )

    @staticmethod
    def command_key(access: AccessContext, key: str) -> dict[str, str]:
        return {
            "org_id": str(access.org_id),
            "issuer": access.principal.issuer,
            "subject": access.principal.subject,
            "key": key,
        }

    async def reserve(self, access: AccessContext, command: CreateKG) -> dict[str, Any]:
        async def transact(session: AsyncClientSession) -> dict[str, Any]:
            current = await self.policy.resolve_access(
                access.principal, [], Action.CREATE_KG, org_id=access.org_id, session=session
            )
            key = self.command_key(current, command.idempotency_key)
            previous = await self.database.catalog_commands.find_one(key, session=session)
            if previous:
                if previous["input"] != command.model_dump(mode="json"):
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT,
                        "The idempotency key was used for different input.",
                    )
                kg = await self.database.kgs.find_one(
                    {"org_id": str(current.org_id), "id": previous["kg_id"]}, session=session
                )
                if kg is None:
                    raise inaccessible_reference()
                return kg
            profile = await self.database.storage_profiles.find_one(
                {"id": str(command.placement_id), "allowed_org_ids": str(current.org_id)},
                session=session,
            )
            domain = await self.database.domain_profiles.find_one(
                {"id": str(command.domain_profile_id)}, session=session
            )
            if profile is None or domain is None:
                raise inaccessible_reference()
            member = await self.database.members.find_one(
                {
                    "org_id": str(current.org_id),
                    "issuer": current.principal.issuer,
                    "subject": current.principal.subject,
                    "suspended": False,
                },
                session=session,
            )
            if member is None:
                raise inaccessible_reference()
            identifier = uuid4()
            kg = {
                "id": str(identifier),
                "org_id": str(current.org_id),
                "name": command.name,
                "storage_profile_id": str(command.placement_id),
                "domain_profile_id": str(command.domain_profile_id),
                "state": "provisioning",
                "revision": 0,
                "visibility_revision": 0,
                "published_revision": 0,
                "generation": str(uuid4()),
            }
            await self.database.kgs.insert_one(kg | {"_id": str(identifier)}, session=session)
            await self.database.kg_grants.insert_one(
                {
                    "org_id": str(current.org_id),
                    "kg_id": str(identifier),
                    "subject_kind": "member",
                    "subject_id": member["id"],
                    "role": "owner",
                },
                session=session,
            )
            await self.database.catalog_commands.insert_one(
                key | {"input": command.model_dump(mode="json"), "kg_id": str(identifier)},
                session=session,
            )
            await self.database.organizations.update_one(
                {"_id": str(current.org_id)}, {"$inc": {"policy_revision": 1}}, session=session
            )
            await self.database.registry_state.update_one(
                {"_id": "placement"}, {"$inc": {"revision": 1}}, upsert=True, session=session
            )
            await assert_active_owners(self.database, str(current.org_id), session)
            await self.database.policy_audit.insert_one(
                {
                    "org_id": str(current.org_id),
                    "kg_id": str(identifier),
                    "operation": "create_kg_and_appoint_owner",
                    "actor_id": member["id"],
                },
                session=session,
            )
            return kg

        async with self.database.client.start_session() as session:
            return await session.with_transaction(transact)

    async def set_state(
        self,
        access: AccessContext,
        kg_id: UUID,
        state: str | None,
        *,
        expected_revision: int,
        idempotency_key: str,
        name: str | None = None,
        restore_deleted: bool = False,
    ) -> dict[str, Any]:
        async def transact(session: AsyncClientSession) -> dict[str, Any]:
            current = await self.policy.resolve_access(
                access.principal, [kg_id], Action.CONFIGURE, org_id=access.org_id, session=session
            )
            key = self.command_key(current, idempotency_key)
            values = {"kg_id": str(kg_id), "state": state, "expected_revision": expected_revision}
            if name is not None:
                values["name"] = name
            if restore_deleted:
                values["restore_deleted"] = True
            previous = await self.database.catalog_commands.find_one(key, session=session)
            if previous:
                if previous["input"] != values:
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT,
                        "The idempotency key was used for different input.",
                    )
                return dict(previous["result"])
            query = {"org_id": str(current.org_id), "id": str(kg_id), "revision": expected_revision}
            kg = await self.database.kgs.find_one(query, session=session)
            if kg is None:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The KG revision changed.")
            if kg["state"] in ("purging", "purged") or (
                kg["state"] == "deleted" and state != "deleted" and not restore_deleted
            ):
                raise DomainError(ErrorCode.FORBIDDEN, "Use explicit restoration for a deleted KG.")
            if restore_deleted and kg["state"] != "deleted":
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The KG is not deleted.")
            changes = {"state": state or kg["state"]}
            if restore_deleted:
                previous_state = kg.get("deleted_from_state")
                if previous_state not in (
                    "ready",
                    "read_only",
                    "archived",
                    "error",
                    "provisioning",
                ):
                    raise DomainError(
                        ErrorCode.PROJECTION_INCONSISTENT,
                        "The retained KG lifecycle is inconsistent.",
                    )
                changes["state"] = previous_state
            elif state == "deleted" and kg["state"] != "deleted":
                changes["deleted_from_state"] = kg["state"]
            if name is not None:
                changes["name"] = name
            await self.database.kgs.update_one(
                query,
                {"$set": changes, "$inc": {"revision": 1, "visibility_revision": 1}},
                session=session,
            )
            await self.database.organizations.update_one(
                {"_id": str(current.org_id)}, {"$inc": {"policy_revision": 1}}, session=session
            )
            result = kg | {
                **changes,
                "revision": expected_revision + 1,
                "visibility_revision": kg["visibility_revision"] + 1,
            }
            await self.database.catalog_commands.insert_one(
                key | {"input": values, "result": result}, session=session
            )
            await assert_active_owners(self.database, str(current.org_id), session)
            await self.database.policy_audit.insert_one(
                {
                    "org_id": str(current.org_id),
                    "kg_id": str(kg_id),
                    "operation": "restore_kg" if restore_deleted else "update_kg",
                    "issuer": current.principal.issuer,
                    "subject": current.principal.subject,
                    "before_state": kg["state"],
                    "after_state": changes["state"],
                    "revision": result["revision"],
                },
                session=session,
            )
            return result

        async with self.database.client.start_session() as session:
            return await session.with_transaction(transact)
