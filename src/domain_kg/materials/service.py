"""Authorized originals and atomic material/job acceptance; publication happens later."""

import asyncio
import hashlib
import json
from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext, Action
from domain_kg.identity.policy import Policy
from domain_kg.jobs.models import JobCommand
from domain_kg.materials.models import (
    AddMaterial,
    BlobRef,
    BlobScope,
    IngestReceipt,
    ReplaceMaterial,
    UploadCommand,
    UploadReceipt,
)
from domain_kg.materials.repository import MaterialRepository
from domain_kg.models import ScopedRef
from domain_kg.storage.blobs import BlobStore
from domain_kg.storage.models import MaterialRecord, MaterialVersionRecord
from domain_kg.storage.router import KGStoreRouter

if TYPE_CHECKING:
    from domain_kg.imports.models import ImportPlan


def digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def expired(value: datetime) -> bool:
    return value.replace(tzinfo=UTC) <= datetime.now(UTC)


class MaterialService:
    def __init__(self, policy: Policy, router: KGStoreRouter) -> None:
        self.policy, self.router = policy, router
        self.repository = MaterialRepository(policy)
        self.database = policy.database

    async def initialize(self) -> None:
        await self.repository.initialize()

    async def _access(self, access: AccessContext, kg: UUID, action: Action) -> AccessContext:
        current = await self.policy.revalidate(access, action=action)
        if kg not in current.kg_ids:
            raise inaccessible_reference()
        return current

    async def _target(
        self,
        access: AccessContext,
        kg: UUID,
        material: UUID,
        session: AsyncClientSession | None = None,
    ) -> dict[str, Any]:
        if not await self.policy.material_visible(access, kg, material, session=session):
            raise inaccessible_reference()
        raw = await self.database.material_access.find_one(
            {"org_id": str(access.org_id), "kg_id": str(kg), "id": str(material)}, session=session
        )
        if raw is None:
            raise inaccessible_reference()
        return raw

    async def upload(
        self, access: AccessContext, command: UploadCommand, stream: AsyncIterable[bytes]
    ) -> UploadReceipt:
        command = UploadCommand.model_validate(command)
        action = Action.REPLACE if command.target_material_id else Action.INGEST
        current = await self._access(access, command.kg_id, action)
        if command.target_material_id:
            await self._target(current, command.kg_id, command.target_material_id)
        stores = await self.router.for_kg(current, command.kg_id)
        blobs = BlobStore(stores.blobs)
        key = self.repository.key(current, command.kg_id, command.idempotency_key)
        identifiers = {
            name: str(uuid4()) for name in ("id", "material_id", "version_id", "object_id")
        }
        if command.target_material_id:
            identifiers["material_id"] = str(command.target_material_id)
        fingerprint = digest(command.model_dump(mode="json"))
        upload_owner = str(uuid4())

        async def reserve(session: AsyncClientSession) -> dict[str, Any]:
            await self.repository.fence(current, action, session)
            previous = await self.database.uploads.find_one(key, session=session)
            if previous:
                if previous["digest"] != fingerprint:
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "Upload key has different input."
                    )
                if previous["state"] == "uploading":
                    if not expired(previous["upload_until"]):
                        raise DomainError(
                            ErrorCode.REVISION_NOT_READY,
                            "This upload is in progress.",
                            retryable=True,
                        )
                    await self.database.uploads.update_one(
                        key,
                        {
                            "$set": {
                                "upload_owner": upload_owner,
                                "upload_until": datetime.now(UTC) + timedelta(minutes=5),
                            }
                        },
                        session=session,
                    )
                return previous
            if command.target_material_id:
                await self._target(current, command.kg_id, command.target_material_id, session)
            await self.repository.quota(current, command.kg_id, command.size_bytes, 0, session)
            raw = (
                key
                | identifiers
                | {
                    "digest": fingerprint,
                    "sha256": command.sha256,
                    "size_bytes": command.size_bytes,
                    "media_type": command.media_type,
                    "state": "uploading",
                    "upload_owner": upload_owner,
                    "upload_until": datetime.now(UTC) + timedelta(minutes=5),
                    "quota_reserved": True,
                    "target_material_id": str(command.target_material_id)
                    if command.target_material_id
                    else None,
                    "expires_at": datetime.now(UTC) + timedelta(hours=24),
                }
            )
            await self.database.uploads.insert_one(raw, session=session)
            return raw

        raw = await self.repository.transaction(reserve)
        if raw["state"] not in ("uploading", "complete", "claimed") or expired(raw["expires_at"]):
            raise inaccessible_reference()
        reference = BlobRef(
            org_id=current.org_id,
            kg_id=command.kg_id,
            material_id=raw["material_id"],
            version_id=raw["version_id"],
            object_id=raw["object_id"],
            sha256=command.sha256,
            size_bytes=command.size_bytes,
            media_type=command.media_type,
        )
        if raw["state"] == "uploading":
            try:
                async with asyncio.timeout(120):
                    await blobs.put(
                        stream,
                        command.sha256,
                        BlobScope.model_validate(
                            reference.model_dump(include=set(BlobScope.model_fields))
                        ),
                        media_type=command.media_type,
                        expected_size=command.size_bytes,
                        object_id=reference.object_id,
                    )
            except (DomainError, TimeoutError) as failure:
                # A previous identical request may have stored the immutable object
                # before losing its Mongo acknowledgement. Verify before recovering.
                try:
                    async for _ in blobs.open(reference):
                        pass
                except DomainError:

                    async def release(session: AsyncClientSession) -> None:
                        removed = await self.database.uploads.update_one(
                            key | {"state": "uploading", "upload_owner": upload_owner},
                            {"$set": {"state": "failed", "quota_reserved": False}},
                            session=session,
                        )
                        if removed.modified_count:
                            await self.repository.quota(
                                current, command.kg_id, -command.size_bytes, 0, session
                            )

                    if isinstance(failure, DomainError) and failure.code in (
                        ErrorCode.EVIDENCE_VALIDATION_FAILED,
                        ErrorCode.UNSUPPORTED_FORMAT,
                        ErrorCode.VALIDATION_ERROR,
                        ErrorCode.QUOTA_EXCEEDED,
                    ):
                        await self.repository.transaction(release)
                    raise failure from None

            async def complete(session: AsyncClientSession) -> None:
                await self.repository.fence(current, action, session)
                result = await self.database.uploads.update_one(
                    key
                    | {
                        "state": "uploading",
                        "upload_owner": upload_owner,
                        "expires_at": {"$gt": datetime.now(UTC)},
                    },
                    {"$set": {"state": "complete", "blob": reference.model_dump(mode="json")}},
                    session=session,
                )
                if not result.matched_count:
                    existing = await self.database.uploads.find_one(key, session=session)
                    if existing is None or existing["state"] not in ("complete", "claimed"):
                        raise inaccessible_reference()

            await self.repository.transaction(complete)
        await self.policy.revalidate(current, action=action)
        return UploadReceipt(
            org_id=current.org_id,
            kg_id=command.kg_id,
            upload_id=raw["id"],
            sha256=command.sha256,
            size_bytes=command.size_bytes,
            media_type=command.media_type,
            expires_at=raw["expires_at"].replace(
                tzinfo=UTC, microsecond=raw["expires_at"].microsecond // 1000 * 1000
            ),
        )

    async def add(self, access: AccessContext, command: AddMaterial) -> IngestReceipt:
        return await self._submit(access, AddMaterial.model_validate(command), Action.INGEST)

    async def replace(self, access: AccessContext, command: ReplaceMaterial) -> IngestReceipt:
        return await self._submit(access, ReplaceMaterial.model_validate(command), Action.REPLACE)

    async def _submit(
        self,
        access: AccessContext,
        command: AddMaterial | ReplaceMaterial,
        action: Action,
        *,
        import_plan: "ImportPlan | None" = None,
    ) -> IngestReceipt:
        current = await self._access(access, command.kg_id, action)
        stores = await self.router.for_kg(current, command.kg_id)
        authority = stores.authority
        await authority._database.version_blobs.create_index(
            [("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True
        )
        data = command.model_dump(mode="json", exclude={"inline_text"})
        if import_plan is not None:
            if (
                not isinstance(command, AddMaterial)
                or action != Action.INGEST
                or command.upload_id != import_plan.source.upload_id
                or command.kg_id != import_plan.kg_id
                or command.acl_mode != "restricted"
            ):
                raise inaccessible_reference()
            data["import_plan"] = import_plan.model_dump(mode="json")
        inline = command.inline_text.encode("utf-8") if command.inline_text is not None else None
        if inline is not None:
            data["inline_sha256"], data["inline_bytes"] = (
                hashlib.sha256(inline).hexdigest(),
                len(inline),
            )
        fingerprint = digest(data | {"operation": action.value})
        key = self.repository.key(current, command.kg_id, command.idempotency_key)

        async def expected(session: AsyncClientSession | None = None) -> None:
            if isinstance(command, ReplaceMaterial):
                target = await self._target(current, command.kg_id, command.material_id, session)
                if target.get("active_version_id") != str(command.expected_active_version):
                    raise DomainError(ErrorCode.VERSION_CONFLICT, "The active version changed.")

        async def intent(session: AsyncClientSession) -> dict[str, Any]:
            await self.repository.fence(current, action, session)
            previous = await self.database.material_commands.find_one(key, session=session)
            if previous:
                if previous["digest"] != fingerprint:
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "Material key has different input."
                    )
                return previous
            await expected(session)
            raw = key | {"digest": fingerprint, "id": str(uuid4()), "state": "pending"}
            await self.database.material_commands.insert_one(raw, session=session)
            return raw

        command_row = await self.repository.transaction(intent)
        if command_row["state"] == "accepted":
            result = IngestReceipt.model_validate(command_row["receipt"])
            await self._target(current, command.kg_id, result.material_id)
            return result
        await expected()
        upload_id = command.upload_id
        if inline is not None:

            async def stream() -> AsyncIterator[bytes]:
                for offset in range(0, len(inline), 65536):
                    yield inline[offset : offset + 65536]

            uploaded = await self.upload(
                current,
                UploadCommand(
                    kg_id=command.kg_id,
                    media_type="text/plain",
                    sha256=hashlib.sha256(inline).hexdigest(),
                    size_bytes=len(inline),
                    idempotency_key=f"material:{command_row['id']}",
                    target_material_id=command.material_id
                    if isinstance(command, ReplaceMaterial)
                    else None,
                ),
                stream(),
            )
            upload_id = uploaded.upload_id

        async def accept(session: AsyncClientSession) -> IngestReceipt:
            await self.repository.fence(current, action, session)
            accepted = await self.database.material_commands.find_one(key, session=session)
            if accepted is None:
                raise inaccessible_reference()
            if accepted["state"] == "accepted":
                return IngestReceipt.model_validate(accepted["receipt"])
            await expected(session)
            upload = await self.database.uploads.find_one(
                {
                    "org_id": str(current.org_id),
                    "kg_id": str(command.kg_id),
                    "id": str(upload_id),
                    "issuer": current.principal.issuer,
                    "subject": current.principal.subject,
                    "state": "complete",
                    "expires_at": {"$gt": datetime.now(UTC)},
                },
                session=session,
            )
            if upload is None:
                raise inaccessible_reference()
            if (
                isinstance(command, ReplaceMaterial)
                and upload["target_material_id"] != str(command.material_id)
            ) or (
                not isinstance(command, ReplaceMaterial)
                and upload["target_material_id"] is not None
            ):
                raise inaccessible_reference()
            material, version = UUID(upload["material_id"]), UUID(upload["version_id"])
            ns = {"org_id": current.org_id, "kg_id": command.kg_id}
            blob = BlobRef.model_validate(upload["blob"])
            if (blob.org_id, blob.kg_id, blob.material_id, blob.version_id) != (
                current.org_id,
                command.kg_id,
                material,
                version,
            ):
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
            if not isinstance(command, ReplaceMaterial):
                await authority.insert(
                    MaterialRecord(**ns, id=material, title=command.title), session=session
                )
                await self.database.material_access.insert_one(
                    {
                        "org_id": str(current.org_id),
                        "kg_id": str(command.kg_id),
                        "id": str(material),
                        "acl_mode": command.acl_mode,
                        "member_ids": [member["id"]],
                        "group_ids": [],
                        "revision": 0,
                        "metadata_epoch": 0,
                        "active_version_id": None,
                        "excluded": False,
                        "tombstoned": False,
                    },
                    session=session,
                )
            await authority.insert(
                MaterialVersionRecord(**ns, id=version, material=ScopedRef(**ns, id=material)),
                session=session,
            )
            source = {
                "org_id": str(current.org_id),
                "kg_id": str(command.kg_id),
                "id": str(version),
            }
            await authority._database.version_blobs.insert_one(
                source
                | {
                    "material_id": str(material),
                    "blob": blob.model_dump(mode="json"),
                    "upload_id": str(upload_id),
                    "state": "staged",
                    "created_at": datetime.now(UTC),
                    "proposed": command.model_dump(
                        mode="json", exclude={"inline_text", "upload_id"}
                    ),
                },
                session=session,
            )
            job = JobCommand(
                **ns,
                id=uuid4(),
                kind="import"
                if import_plan is not None
                else ("replace" if isinstance(command, ReplaceMaterial) else "ingest"),
                target_id=version,
                idempotency_key=command.idempotency_key,
            )
            import_fields: dict[str, str] = {}
            if import_plan is not None:
                from domain_kg.imports.admission import admit_import

                origin = await admit_import(self, current, import_plan, blob, job.id, session)
                import_fields = {
                    "import_plan_id": str(origin.plan_id),
                    "import_plan_hash": origin.plan_hash,
                }
            metadata_target = await self.database.material_access.find_one(
                {"org_id": str(current.org_id), "kg_id": str(command.kg_id), "id": str(material)},
                session=session,
            )
            if metadata_target is None:
                raise inaccessible_reference()
            await self.database.jobs.insert_one(
                job.model_dump(mode="json")
                | import_fields
                | {
                    "actor": current.principal.model_dump(mode="json"),
                    "material_id": str(material),
                    "stage": "queued",
                    "fence": 0,
                    "attempts": 0,
                    "created_at": datetime.now(UTC),
                    "command_id": command_row["id"],
                    "expected_metadata_epoch": metadata_target.get("metadata_epoch", 0),
                    "expected_active_version": str(command.expected_active_version)
                    if isinstance(command, ReplaceMaterial)
                    else None,
                },
                session=session,
            )
            await self.database.outbox.insert_one(
                {
                    "org_id": str(current.org_id),
                    "kg_id": str(command.kg_id),
                    "id": str(job.id),
                    "kind": "job_available",
                    "delivered": False,
                    "created_at": datetime.now(UTC),
                },
                session=session,
            )
            await self.repository.quota(
                current, command.kg_id, -blob.size_bytes, blob.size_bytes, session
            )
            await self.database.uploads.update_one(
                {"_id": upload["_id"]},
                {"$set": {"state": "claimed", "command_id": command_row["id"]}},
                session=session,
            )
            result = IngestReceipt(**ns, material_id=material, version_id=version, job_id=job.id)
            await self.database.material_commands.update_one(
                key,
                {
                    "$set": {
                        "state": "accepted",
                        "receipt": result.model_dump(mode="json"),
                    }
                },
                session=session,
            )
            return result

        result = await self.repository.transaction(accept)
        await self._target(current, command.kg_id, result.material_id)
        return result

    async def original(
        self, access: AccessContext, kg: UUID, material: UUID, version: UUID
    ) -> AsyncGenerator[bytes, None]:
        current = await self._access(access, kg, Action.READ)
        await self._target(current, kg, material)
        stores = await self.router.for_kg(current, kg)
        ns = {"org_id": current.org_id, "kg_id": kg}
        record = await stores.authority.get_material_version(ScopedRef(**ns, id=version))
        if record is None or record.material.id != material:
            raise inaccessible_reference()
        raw = await stores.authority._database.version_blobs.find_one(
            {
                "org_id": str(current.org_id),
                "kg_id": str(kg),
                "id": str(version),
                "material_id": str(material),
            }
        )
        if raw is None or raw["state"] not in ("staged", "accepted", "retained"):
            raise inaccessible_reference()
        if raw["state"] == "staged" and not any(
            grant.kg_id == kg and grant.role in ("owner", "editor") for grant in current.grants
        ):
            raise inaccessible_reference()
        blob = BlobRef.model_validate(raw["blob"])
        if (blob.org_id, blob.kg_id, blob.material_id, blob.version_id) != (
            current.org_id,
            kg,
            material,
            version,
        ):
            raise inaccessible_reference()
        async for part in BlobStore(stores.blobs).open(blob):
            await self._target(current, kg, material)
            yield part
        await self._target(current, kg, material)

    async def expire_uploads(self, access: AccessContext) -> None:
        current = await self.policy.revalidate(access, action=Action.INGEST)
        for kg in current.kg_ids:
            stores = await self.router.for_kg(current, kg)
            scope = {"org_id": str(current.org_id), "kg_id": str(kg)}
            async for raw in self.database.uploads.find(
                scope
                | {
                    "state": {"$in": ["uploading", "complete", "failed", "expired"]},
                    "expires_at": {"$lte": datetime.now(UTC)},
                }
            ).batch_size(25):
                query = scope | {"id": raw["id"]}
                fenced = await self.database.uploads.find_one_and_update(
                    query
                    | {
                        "state": {"$in": ["uploading", "complete", "failed", "expired"]},
                    },
                    {"$set": {"state": "expired"}},
                )
                if fenced is None:
                    continue
                reference = BlobRef(
                    org_id=current.org_id,
                    kg_id=kg,
                    material_id=raw["material_id"],
                    version_id=raw["version_id"],
                    object_id=raw["object_id"],
                    sha256=raw["sha256"],
                    size_bytes=raw["size_bytes"],
                    media_type=raw["media_type"],
                )
                await BlobStore(stores.blobs).remove_unclaimed(reference)

                async def release(
                    session: AsyncClientSession,
                    *,
                    upload_query: dict[str, Any] = query,
                    upload_kg: UUID = kg,
                    upload_size: int = raw["size_bytes"] if raw.get("quota_reserved", True) else 0,
                ) -> None:
                    await self.repository.fence(current, Action.INGEST, session)
                    result = await self.database.uploads.update_one(
                        upload_query | {"state": "expired"},
                        {"$set": {"state": "removed"}},
                        session=session,
                    )
                    if result.modified_count:
                        await self.repository.quota(current, upload_kg, -upload_size, 0, session)

                await self.repository.transaction(release)
