"""Actor-owned import manifests and resumable admission of independently durable jobs."""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID, uuid5

from pydantic import Field, model_validator
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.models import JobReceipt
from domain_kg.jobs.queue import JobQueue
from domain_kg.materials.models import AddMaterial, MaterialMetadata, ReplaceMaterial
from domain_kg.materials.service import MaterialService, digest
from domain_kg.models import (
    SHA256,
    FrozenModel,
    IdempotencyKey,
    Namespace,
    NonNegative,
    UTCDateTime,
)

logger = logging.getLogger(__name__)
_DENIED = frozenset(
    {
        ErrorCode.UNAUTHENTICATED,
        ErrorCode.FORBIDDEN,
        ErrorCode.RESOURCE_NOT_ACCESSIBLE,
        ErrorCode.POLICY_CHANGED,
    }
)


class ImportFile(FrozenModel):
    path: Annotated[str, Field(min_length=1, max_length=990)]
    sha256: SHA256
    size_bytes: Annotated[int, Field(ge=0, le=50 * 1024 * 1024, strict=True)]
    media_type: Annotated[str, Field(min_length=1, max_length=150)]

    @model_validator(mode="after")
    def relative_path(self) -> Self:
        if "\\" in self.path or any(
            not part or part in (".", "..") or any(ord(c) < 32 for c in part)
            for part in self.path.split("/")
        ):
            raise ValueError("provide a safe relative file path")
        return self


class CreateImportRun(FrozenModel):
    files: Annotated[tuple[ImportFile, ...], Field(min_length=1, max_length=100)]
    idempotency_key: IdempotencyKey

    @model_validator(mode="after")
    def distinct_paths(self) -> Self:
        if len({item.path for item in self.files}) != len(self.files):
            raise ValueError("import paths must be distinct")
        return self


class ImportRunSubmission(FrozenModel):
    upload_id: UUID | None = None
    job_id: UUID | None = None
    material_id: UUID | None = None
    expected_active_version: UUID | None = None
    expected_revision: NonNegative | None = None

    @model_validator(mode="after")
    def one_source(self) -> Self:
        if (self.upload_id is None) == (self.job_id is None):
            raise ValueError("provide exactly one upload ID or existing job ID")
        if (self.material_id is None) != (self.expected_active_version is None):
            raise ValueError("replacement requires material and expected active version")
        if self.expected_revision is not None and self.material_id is None:
            raise ValueError("a metadata revision requires a replacement target")
        if self.job_id is not None and self.material_id is not None:
            raise ValueError("replacement fields require an uploaded original")
        return self


class ImportRunItem(ImportFile):
    index: NonNegative
    state: Literal["waiting", "submitted", "failed"] = "waiting"
    job: JobReceipt | None = None
    code: ErrorCode | None = None
    message: str = ""
    can_resume: bool = False
    status_unavailable: bool = False


class ImportRun(Namespace):
    id: UUID
    created_at: UTCDateTime
    items: Annotated[tuple[ImportRunItem, ...], Field(min_length=1, max_length=100)]


class ImportRunHead(FrozenModel):
    id: UUID
    created_at: UTCDateTime
    file_count: NonNegative


class ImportRuns:
    def __init__(self, materials: MaterialService, queue: JobQueue) -> None:
        self.materials, self.queue = materials, queue
        self.database, self.policy = materials.database, materials.policy

    async def initialize(self) -> None:
        await self.database.import_runs.create_index(
            [("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True
        )
        await self.database.import_runs.create_index(
            [
                ("org_id", 1),
                ("kg_id", 1),
                ("issuer", 1),
                ("subject", 1),
                ("kind", 1),
                ("created_at", -1),
                ("id", -1),
            ]
        )
        await self.database.import_run_items.create_index(
            [("org_id", 1), ("kg_id", 1), ("run_id", 1), ("index", 1)], unique=True
        )

    @staticmethod
    def _binding(access: AccessContext, kg: UUID) -> dict[str, str]:
        return {
            "org_id": str(access.org_id),
            "kg_id": str(kg),
            "issuer": access.principal.issuer,
            "subject": access.principal.subject,
            "kind": access.principal.kind,
        }

    async def _access(self, access: AccessContext, kg: UUID, action: Action) -> AccessContext:
        current = await self.policy.revalidate(access, action=action)
        if kg not in current.kg_ids:
            raise inaccessible_reference()
        return current

    async def _authorize_job(self, access: AccessContext, kg: UUID, identifier: UUID) -> None:
        visible = await self.queue._visible_job(access, identifier, Action.READ)
        if (visible["org_id"], visible["kg_id"]) != (str(access.org_id), str(kg)):
            raise inaccessible_reference()

    async def _run(self, access: AccessContext, kg: UUID, identifier: UUID) -> dict[str, Any]:
        raw = await self.database.import_runs.find_one(
            self._binding(access, kg) | {"id": str(identifier)}
        )
        if raw is None:
            raise inaccessible_reference()
        return raw

    async def create(self, access: AccessContext, kg: UUID, command: CreateImportRun) -> ImportRun:
        command = CreateImportRun.model_validate(command)
        current = await self._access(access, kg, Action.INGEST)
        if len(command.model_dump_json().encode()) > 1024 * 1024:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Import manifest exceeds 1 MiB.")
        binding = self._binding(current, kg)
        identifier = uuid5(current.org_id, digest(binding | {"key": command.idempotency_key}))
        fingerprint = digest(command.model_dump(mode="json"))

        async def reserve(session: AsyncClientSession) -> None:
            await self.materials.repository.fence(current, Action.INGEST, session)
            key = binding | {"id": str(identifier)}
            previous = await self.database.import_runs.find_one(key, session=session)
            if previous is not None:
                if previous["digest"] != fingerprint:
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "Import key has different input."
                    )
                return
            await self.database.import_runs.insert_one(
                key
                | {
                    "digest": fingerprint,
                    "created_at": datetime.now(UTC),
                    "files": [item.model_dump(mode="json") for item in command.files],
                },
                session=session,
            )

        await self.materials.repository.transaction(reserve)
        return await self.get(current, kg, identifier)

    async def list(
        self, access: AccessContext, kg: UUID, *, limit: int = 10
    ) -> tuple[ImportRunHead, ...]:
        if isinstance(limit, bool) or not 1 <= limit <= 10:
            raise DomainError(ErrorCode.VALIDATION_ERROR, "Choose one to ten recent imports.")
        current = await self._access(access, kg, Action.READ)
        rows = (
            await self.database.import_runs.find(self._binding(current, kg))
            .sort([("created_at", -1), ("id", -1)])
            .limit(limit)
            .to_list(length=limit)
        )
        result = tuple(
            ImportRunHead(
                id=row["id"],
                created_at=row["created_at"].replace(tzinfo=UTC),
                file_count=len(row["files"]),
            )
            for row in rows
        )
        await self._access(current, kg, Action.READ)
        return result

    async def head(self, access: AccessContext, kg: UUID, identifier: UUID) -> ImportRunHead:
        current = await self._access(access, kg, Action.READ)
        raw = await self._run(current, kg, identifier)
        result = ImportRunHead(
            id=raw["id"],
            created_at=raw["created_at"].replace(tzinfo=UTC),
            file_count=len(raw["files"]),
        )
        await self._access(current, kg, Action.READ)
        return result

    async def get(self, access: AccessContext, kg: UUID, identifier: UUID) -> ImportRun:
        current = await self._access(access, kg, Action.READ)
        raw = await self._run(current, kg, identifier)
        commands = {
            row["index"]: row
            async for row in self.database.import_run_items.find(
                self._binding(current, kg) | {"run_id": str(identifier)}
            )
        }
        items = []
        for offset in range(0, len(raw["files"]), 4):
            items.extend(
                await asyncio.gather(
                    *(
                        self._item(
                            current,
                            kg,
                            ImportFile.model_validate(raw["files"][index]),
                            index,
                            commands.get(index),
                        )
                        for index in range(offset, min(offset + 4, len(raw["files"])))
                    )
                )
            )
        # Progress is authority metadata, not a graph snapshot. Recheck the exact
        # admitted sources so an unrelated publication cannot cancel monitoring.
        job_ids = [UUID(row["job_id"]) for row in commands.values() if row.get("job_id")]
        for offset in range(0, len(job_ids), 4):
            await asyncio.gather(
                *(
                    self._authorize_job(current, kg, identifier)
                    for identifier in job_ids[offset : offset + 4]
                )
            )
        await self._access(current, kg, Action.READ)
        return ImportRun(
            org_id=current.org_id,
            kg_id=kg,
            id=identifier,
            created_at=raw["created_at"].replace(tzinfo=UTC),
            items=tuple(items),
        )

    async def _get_item(
        self, access: AccessContext, kg: UUID, identifier: UUID, index: int
    ) -> ImportRunItem:
        current = await self._access(access, kg, Action.READ)
        raw = await self._run(current, kg, identifier)
        if isinstance(index, bool) or not 0 <= index < len(raw["files"]):
            raise inaccessible_reference()
        row = await self.database.import_run_items.find_one(
            self._binding(current, kg) | {"run_id": str(identifier), "index": index}
        )
        item = await self._item(
            current, kg, ImportFile.model_validate(raw["files"][index]), index, row
        )
        if row and row.get("job_id"):
            await self._authorize_job(current, kg, UUID(row["job_id"]))
        await self._access(current, kg, Action.READ)
        return item

    async def _item(
        self,
        access: AccessContext,
        kg: UUID,
        source: ImportFile,
        index: int,
        row: dict[str, Any] | None,
    ) -> ImportRunItem:
        values = source.model_dump() | {"index": index}
        if row is None:
            return ImportRunItem.model_validate(values)
        if row.get("job_id"):
            try:
                job = await self.queue.get(access, UUID(row["job_id"]))
                self._matching_job(access, kg, source, job)
            except DomainError as error:
                if error.code in _DENIED:
                    raise
                await self._authorize_job(access, kg, UUID(row["job_id"]))
                # One unavailable projection must not prevent inspection of other files.
                return ImportRunItem.model_validate(
                    values
                    | {
                        "state": "failed",
                        "code": error.code,
                        "message": "Job status is temporarily unavailable. Refresh to retry.",
                        "status_unavailable": True,
                    }
                )
            except Exception as error:
                await self._authorize_job(access, kg, UUID(row["job_id"]))
                logger.error("Import job read failed: kind=%s", type(error).__name__)
                return ImportRunItem.model_validate(
                    values
                    | {
                        "state": "failed",
                        "code": ErrorCode.BACKEND_UNAVAILABLE,
                        "message": "Job status is temporarily unavailable. Refresh to retry.",
                        "status_unavailable": True,
                    }
                )
            return ImportRunItem.model_validate(values | {"state": "submitted", "job": job})
        return ImportRunItem.model_validate(
            values
            | {
                "state": "failed",
                "code": row.get("code"),
                "message": row.get("message", "Submission has not finished. Resume this file."),
                "can_resume": row.get("command", {}).get("upload_id") is not None,
            }
        )

    @staticmethod
    def _matching_job(access: AccessContext, kg: UUID, source: ImportFile, job: JobReceipt) -> None:
        if (
            job.org_id != access.org_id
            or job.kg_id != kg
            or job.source is None
            or job.source.source != "folder:" + source.path
            or job.source.sha256 != source.sha256
        ):
            raise inaccessible_reference()

    async def submit(
        self,
        access: AccessContext,
        kg: UUID,
        identifier: UUID,
        index: int,
        command: ImportRunSubmission,
    ) -> ImportRunItem:
        command = ImportRunSubmission.model_validate(command)
        action = Action.REPLACE if command.material_id is not None else Action.INGEST
        current = await self._access(access, kg, action)
        raw = await self._run(current, kg, identifier)
        if isinstance(index, bool) or not 0 <= index < len(raw["files"]):
            raise inaccessible_reference()
        source = ImportFile.model_validate(raw["files"][index])
        key = self._binding(current, kg) | {"run_id": str(identifier), "index": index}
        fingerprint = digest(command.model_dump(mode="json"))
        previous = await self.database.import_run_items.find_one(key)
        if previous is not None:
            if previous["digest"] != fingerprint:
                raise DomainError(
                    ErrorCode.IDEMPOTENCY_CONFLICT, "Import item has different input."
                )
            return await self._admit(current, kg, identifier, index, source, command)
        if command.job_id is not None:
            job = await self.queue.get(current, command.job_id)
            self._matching_job(current, kg, source, job)
        else:
            upload = await self.database.uploads.find_one(
                {
                    "org_id": str(current.org_id),
                    "kg_id": str(kg),
                    "issuer": current.principal.issuer,
                    "subject": current.principal.subject,
                    "id": str(command.upload_id),
                    "state": {"$in": ["complete", "claimed"]},
                }
            )
            if upload is None or any(
                upload.get(key) != getattr(source, key)
                for key in ("sha256", "size_bytes", "media_type")
            ):
                raise inaccessible_reference()
            if upload.get("target_material_id") != (
                str(command.material_id) if command.material_id is not None else None
            ):
                raise inaccessible_reference()

        async def reserve(session: AsyncClientSession) -> None:
            await self.materials.repository.fence(current, action, session)
            previous = await self.database.import_run_items.find_one(key, session=session)
            if previous is not None:
                if previous["digest"] != fingerprint:
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "Import item has different input."
                    )
                return
            metadata: dict[str, Any] = {}
            if command.material_id is not None:
                target = await self.materials._target(current, kg, command.material_id, session)
                if target.get("active_version_id") != str(command.expected_active_version):
                    raise DomainError(ErrorCode.VERSION_CONFLICT, "The active version changed.")
                if (
                    command.expected_revision is not None
                    and target["revision"] != command.expected_revision
                ):
                    raise DomainError(ErrorCode.VERSION_CONFLICT, "The material revision changed.")
                previous_metadata = MaterialMetadata.model_validate(target["metadata"])
                if (
                    previous_metadata.material_id != command.material_id
                    or str(previous_metadata.version_id) != target["active_version_id"]
                ):
                    raise inaccessible_reference()
                metadata = previous_metadata.model_dump(
                    mode="json", include={"tags", "author", "language"}
                )
            await self.database.import_run_items.insert_one(
                key
                | {
                    "digest": fingerprint,
                    "command": command.model_dump(mode="json"),
                    "metadata": metadata,
                },
                session=session,
            )

        # The exact upload command survives a crash before/after material admission.
        await self.materials.repository.transaction(reserve)
        return await self._admit(current, kg, identifier, index, source, command)

    async def resume(
        self, access: AccessContext, kg: UUID, identifier: UUID, index: int
    ) -> ImportRunItem:
        current = await self._access(access, kg, Action.READ)
        raw = await self._run(current, kg, identifier)
        if isinstance(index, bool) or not 0 <= index < len(raw["files"]):
            raise inaccessible_reference()
        row = await self.database.import_run_items.find_one(
            self._binding(current, kg) | {"run_id": str(identifier), "index": index}
        )
        if row is None:
            raise DomainError(ErrorCode.VERSION_CONFLICT, "Reselect this file to upload it first.")
        if row.get("job_id") is not None:
            return await self._get_item(current, kg, identifier, index)
        return await self.submit(
            current, kg, identifier, index, ImportRunSubmission.model_validate(row["command"])
        )

    async def _admit(
        self,
        access: AccessContext,
        kg: UUID,
        identifier: UUID,
        index: int,
        source: ImportFile,
        command: ImportRunSubmission,
    ) -> ImportRunItem:
        key = self._binding(access, kg) | {"run_id": str(identifier), "index": index}
        row = await self.database.import_run_items.find_one(key)
        if row is None:
            raise inaccessible_reference()
        if row.get("job_id") is None:
            try:
                if command.job_id is not None:
                    job_id = command.job_id
                else:
                    values = {
                        "kg_id": kg,
                        "title": source.path,
                        "source": "folder:" + source.path,
                        "upload_id": command.upload_id,
                        "idempotency_key": str(uuid5(identifier, str(index))),
                    } | row.get("metadata", {})
                    receipt = (
                        await self.materials.add(access, AddMaterial.model_validate(values))
                        if command.material_id is None
                        else await self.materials.replace(
                            access,
                            ReplaceMaterial.model_validate(
                                values
                                | {
                                    "material_id": command.material_id,
                                    "expected_active_version": command.expected_active_version,
                                    "expected_revision": command.expected_revision,
                                }
                            ),
                        )
                    )
                    job_id = receipt.job_id
                await self.database.import_run_items.update_one(
                    key | {"job_id": {"$exists": False}},
                    {"$set": {"job_id": str(job_id)}, "$unset": {"code": "", "message": ""}},
                )
            except DomainError as error:
                if error.code in _DENIED:
                    raise
                await self.database.import_run_items.update_one(
                    key | {"job_id": {"$exists": False}},
                    {"$set": {"code": error.code.value, "message": error.message}},
                )
            except Exception as error:
                logger.error(
                    "Import admission interrupted: run=%s kind=%s", identifier, type(error).__name__
                )
                await self.database.import_run_items.update_one(
                    key | {"job_id": {"$exists": False}},
                    {
                        "$set": {
                            "code": ErrorCode.BACKEND_UNAVAILABLE.value,
                            "message": "Submission was interrupted. Resume this file.",
                        }
                    },
                )
        # Fresh job and exact source authorization protect the returned metadata.
        return await self._get_item(access, kg, identifier, index)
