"""Current-policy material visibility fences; retained evidence remains immutable."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.queue import TERMINAL, JobQueue
from domain_kg.materials.models import (
    RemovalReceipt,
    RemoveMaterial,
    RestorationReceipt,
    RestoreMaterial,
    RestoreReadPermit,
)
from domain_kg.materials.service import MaterialService, digest
from domain_kg.models import SourceVersion
from domain_kg.revisions.journal import PublicationJournal


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
                        "purge_after": (
                            timestamp + timedelta(days=30) if command.action == "delete" else None
                        ),
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

    async def restore(self, access: AccessContext, command: RestoreMaterial) -> RestorationReceipt:
        command = RestoreMaterial.model_validate(command)
        try:
            return await self._restore(access, command)
        except DomainError:
            # Another identical request may commit while this request verifies
            # the retained view. Recheck CURRENT authority before replaying it.
            current = await self.materials._access(access, command.kg_id, Action.REPLACE)
            await self._record(current, command.kg_id, command.material_id)
            key = self.transactions.key(current, command.kg_id, command.idempotency_key)
            saved = await self.database.material_commands.find_one(key)
            fingerprint = digest(
                {"operation": "restore", "command": command.model_dump(mode="json")}
            )
            if (
                saved
                and saved.get("operation") == "restore"
                and saved["state"] == "accepted"
                and saved["digest"] == fingerprint
            ):
                return self._restore_receipt(current, command, saved)
            raise

    @staticmethod
    def _restore_receipt(
        access: AccessContext, command: RestoreMaterial, saved: dict[str, Any]
    ) -> RestorationReceipt:
        receipt = RestorationReceipt.model_validate(saved["receipt"])
        if (
            receipt.org_id,
            receipt.kg_id,
            receipt.material_id,
            receipt.revision,
            receipt.state,
        ) != (
            access.org_id,
            command.kg_id,
            command.material_id,
            command.expected_revision + 1,
            "restored",
        ):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Restoration receipt ownership differs."
            )
        return receipt

    async def _restore(self, access: AccessContext, command: RestoreMaterial) -> RestorationReceipt:
        from domain_kg.materials.restoration import authorize_restore_read
        from domain_kg.revisions.publication import accepted_document, verify_document

        command = RestoreMaterial.model_validate(command)
        current = await self.materials._access(access, command.kg_id, Action.REPLACE)
        key = self.transactions.key(current, command.kg_id, command.idempotency_key)
        fingerprint = digest({"operation": "restore", "command": command.model_dump(mode="json")})
        namespace = {"org_id": str(current.org_id), "kg_id": str(command.kg_id)}

        def expected(row: dict[str, Any]) -> None:
            if row["revision"] != command.expected_revision or not (
                row.get("excluded") or row.get("tombstoned")
            ):
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The restoration target changed.")
            if row.get("tombstoned") and (
                not row.get("purge_after")
                or row["purge_after"].replace(tzinfo=UTC) <= datetime.now(UTC)
            ):
                raise DomainError(
                    ErrorCode.VERSION_CONFLICT, "The source is outside live retention."
                )
            if not row.get("active_version_id"):
                raise DomainError(ErrorCode.REVISION_NOT_READY, "The source needs reprocessing.")

        async def prepare_intent(session: AsyncClientSession) -> dict[str, Any]:
            await self.transactions.fence(current, Action.REPLACE, session)
            row = await self._record(current, command.kg_id, command.material_id, session=session)
            previous = await self.database.material_commands.find_one(key, session=session)
            if previous:
                if previous["digest"] != fingerprint:
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "Material key has different input."
                    )
                if previous["state"] == "accepted":
                    return dict(previous)
            expected(row)
            source = SourceVersion(
                org_id=current.org_id,
                kg_id=command.kg_id,
                material_id=command.material_id,
                version_id=UUID(row["active_version_id"]),
            )
            if previous and previous["source"] != source.model_dump(mode="json"):
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The retained source changed.")
            expires = datetime.now(UTC) + timedelta(minutes=5)
            if previous:
                await self.database.material_commands.update_one(
                    key, {"$set": {"expires_at": expires}}, session=session
                )
                return dict(previous) | {"expires_at": expires}
            intent = key | {
                "id": str(uuid4()),
                "digest": fingerprint,
                "operation": "restore",
                "state": "pending",
                "source": source.model_dump(mode="json"),
                "expected_revision": command.expected_revision,
                "actor": current.principal.model_dump(mode="json"),
                "created_at": datetime.now(UTC),
                "expires_at": expires,
            }
            await self.database.material_commands.insert_one(intent, session=session)
            return intent

        intent = await self.transactions.transaction(prepare_intent)
        if intent["state"] == "accepted":
            await self._record(current, command.kg_id, command.material_id)
            return self._restore_receipt(current, command, intent)
        source = SourceVersion.model_validate(intent["source"])
        view = await PublicationJournal(self.queue).load_metadata(current, command.kg_id)
        binding = next((item for item in view.projections if item.source == source), None)
        if binding is None or view.manifest_id is None:
            raise DomainError(
                ErrorCode.REVISION_NOT_READY, "The retained source needs reprocessing."
            )
        kg = await self.database.kgs.find_one(
            {
                "org_id": namespace["org_id"],
                "id": namespace["kg_id"],
                "published_manifest_id": str(view.manifest_id),
                "generation": str(view.generation),
            }
        )
        if kg is None:
            raise DomainError(ErrorCode.POLICY_CHANGED, "The retained publication changed.")
        permit = RestoreReadPermit(
            org_id=current.org_id,
            kg_id=command.kg_id,
            command_id=intent["id"],
            source=source,
            expected_revision=command.expected_revision,
            view_manifest_id=view.manifest_id,
            projection_manifest_id=binding.manifest_id,
            generation=view.generation,
            visibility_revision=kg["visibility_revision"],
        )
        document, batch, receipts = await accepted_document(
            self.queue, current, binding, restoration=permit
        )
        stores = await self.materials.router.for_kg(current, command.kg_id)
        if await verify_document(stores, document, batch) != receipts:
            raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Retained receipts differ.")
        await authorize_restore_read(self.queue, current, source, permit)

        async def activate(session: AsyncClientSession) -> RestorationReceipt:
            await self.transactions.fence(current, Action.REPLACE, session)
            row = await self._record(current, command.kg_id, command.material_id, session=session)
            saved = await self.database.material_commands.find_one(key, session=session)
            if saved is None or saved["digest"] != fingerprint:
                raise inaccessible_reference()
            if saved["state"] == "accepted":
                return self._restore_receipt(current, command, saved)
            expected(row)
            if row["active_version_id"] != str(source.version_id):
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The retained source changed.")
            timestamp = datetime.now(UTC)
            changed = await self.database.kgs.update_one(
                {
                    "org_id": namespace["org_id"],
                    "id": namespace["kg_id"],
                    "published_manifest_id": str(permit.view_manifest_id),
                    "generation": str(permit.generation),
                    "visibility_revision": permit.visibility_revision,
                },
                {"$inc": {"visibility_revision": 1}},
                session=session,
            )
            if not changed.matched_count:
                raise DomainError(ErrorCode.POLICY_CHANGED, "The retained publication changed.")
            changed = await self.database.material_access.update_one(
                namespace | {"id": str(command.material_id), "revision": command.expected_revision},
                {
                    "$set": {
                        "excluded": False,
                        "tombstoned": False,
                        "restored_at": timestamp,
                        "purge_after": None,
                    },
                    "$inc": {"revision": 1},
                },
                session=session,
            )
            if not changed.matched_count:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The restoration target changed.")
            receipt = RestorationReceipt(
                org_id=current.org_id,
                kg_id=command.kg_id,
                material_id=command.material_id,
                revision=command.expected_revision + 1,
                state="restored",
            )
            await self.database.material_commands.update_one(
                key | {"state": "pending"},
                {
                    "$set": {
                        "state": "accepted",
                        "receipt": receipt.model_dump(mode="json"),
                        "accepted_at": timestamp,
                    }
                },
                session=session,
            )
            await self.database.audit.insert_one(
                namespace
                | {
                    "id": str(uuid4()),
                    "kind": "material_restore",
                    "material_id": str(command.material_id),
                    "actor": current.principal.model_dump(mode="json"),
                    "revision": receipt.revision,
                    "created_at": timestamp,
                },
                session=session,
            )
            return receipt

        result = await self.transactions.transaction(activate)
        await self.materials._target(current, command.kg_id, command.material_id)
        return result
