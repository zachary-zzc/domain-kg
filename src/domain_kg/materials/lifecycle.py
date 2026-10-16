"""Current-policy material visibility fences; retained evidence remains immutable."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.queue import TERMINAL, JobQueue
from domain_kg.materials.models import RemovalReceipt, RemoveMaterial
from domain_kg.materials.service import MaterialService, digest


class MaterialLifecycle:
    def __init__(self, materials: MaterialService, queue: JobQueue) -> None:
        self.materials, self.queue = materials, queue
        self.policy, self.database = materials.policy, materials.database
        self.transactions = materials.repository

    async def _record(
        self,
        access: AccessContext,
        kg: UUID,
        material: UUID,
        *,
        session: AsyncClientSession | None = None,
    ) -> dict[str, Any]:
        row = await self.policy._material_acl_record(access, kg, material, session=session)
        if row is None:
            raise inaccessible_reference()
        return row

    async def remove(self, access: AccessContext, command: RemoveMaterial) -> RemovalReceipt:
        command = RemoveMaterial.model_validate(command)
        current = await self.materials._access(access, command.kg_id, Action.REMOVE)
        key = self.transactions.key(current, command.kg_id, command.idempotency_key)
        fingerprint = digest({"operation": "remove", "command": command.model_dump(mode="json")})

        async def commit(session: AsyncClientSession) -> RemovalReceipt:
            if command.dry_run:
                await self.policy.revalidate(current, action=Action.REMOVE, session=session)
            else:
                await self.transactions.fence(current, Action.REMOVE, session)
            row = await self._record(current, command.kg_id, command.material_id, session=session)
            previous = await self.database.material_commands.find_one(key, session=session)
            if previous and not command.dry_run:
                if previous["digest"] != fingerprint:
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "Material key has different input."
                    )
                return RemovalReceipt.model_validate(previous["receipt"])
            if row["revision"] != command.expected_revision:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The material revision changed.")
            if row.get("tombstoned") or (row.get("excluded") and command.action == "exclude"):
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The material is already withdrawn.")
            if command.dry_run:
                return RemovalReceipt(
                    org_id=current.org_id,
                    kg_id=command.kg_id,
                    material_id=command.material_id,
                    revision=row["revision"],
                    dry_run=True,
                )
            timestamp = datetime.now(UTC)
            namespace = {"org_id": str(current.org_id), "kg_id": str(command.kg_id)}
            changed = await self.database.material_access.update_one(
                namespace | {"id": str(command.material_id), "revision": command.expected_revision},
                {
                    "$set": {
                        "tombstoned": command.action == "delete",
                        "excluded": row.get("excluded", False) or command.action == "exclude",
                        "removed_at": timestamp,
                        "purge_after": timestamp + timedelta(days=30),
                        "removal_action": command.action,
                    },
                    "$inc": {"revision": 1},
                },
                session=session,
            )
            if not changed.matched_count:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The material revision changed.")
            await self.database.kgs.update_one(
                {"org_id": namespace["org_id"], "id": namespace["kg_id"]},
                {"$inc": {"visibility_revision": 1}},
                session=session,
            )
            await self.database.jobs.update_many(
                namespace
                | {
                    "$or": [
                        {"material_id": str(command.material_id)},
                        {"material_ids": str(command.material_id)},
                    ],
                    "kind": {"$in": ["ingest", "replace", "repair", "project"]},
                    "stage": {"$nin": list(TERMINAL)},
                },
                {
                    "$set": {
                        "stage": "cancelled",
                        "error_code": "SOURCE_REMOVED",
                        "lease_until": timestamp,
                        "cancelled_at": timestamp,
                    },
                    "$inc": {"fence": 1},
                },
                session=session,
            )
            receipt = RemovalReceipt(
                org_id=current.org_id,
                kg_id=command.kg_id,
                material_id=command.material_id,
                revision=row["revision"] + 1,
                dry_run=False,
            )
            await self.database.material_commands.insert_one(
                key
                | {
                    "id": str(uuid4()),
                    "digest": fingerprint,
                    "operation": "remove",
                    "state": "accepted",
                    "receipt": receipt.model_dump(mode="json"),
                    "created_at": timestamp,
                },
                session=session,
            )
            for collection, kind in (
                ("outbox", "material_withdrawn"),
                ("audit", "material_remove"),
            ):
                await self.database[collection].insert_one(
                    namespace
                    | {
                        "id": str(uuid4()),
                        "material_id": str(command.material_id),
                        "kind": kind,
                        "action": command.action,
                        "revision": receipt.revision,
                        "actor": current.principal.model_dump(mode="json"),
                        "created_at": timestamp,
                        "delivered": False,
                    },
                    session=session,
                )
            return receipt

        result = await self.transactions.transaction(commit)
        await self.policy.revalidate(current, action=Action.REMOVE)
        await self._record(current, command.kg_id, command.material_id)
        return result
