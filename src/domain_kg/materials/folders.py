"""Folder manifests contain client-supplied content, never executable server paths."""

import logging
from typing import Annotated, Literal, Self
from uuid import UUID, uuid5

from pydantic import Field, model_validator
from pymongo import ReturnDocument

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext, Action
from domain_kg.materials.models import AddMaterial, IngestReceipt, MaterialInput, ReplaceMaterial
from domain_kg.materials.service import MaterialService, digest
from domain_kg.models import FrozenModel, IdempotencyKey, Namespace, NonNegative

logger = logging.getLogger(__name__)


class FolderItem(MaterialInput):
    path: Annotated[str, Field(min_length=1, max_length=990)]
    material_id: UUID | None = None
    expected_active_version: UUID | None = None
    expected_revision: NonNegative | None = None

    @model_validator(mode="after")
    def relative_path_and_replacement(self) -> Self:
        if "\\" in self.path or any(
            not part or part in (".", "..") or any(ord(c) < 32 for c in part)
            for part in self.path.split("/")
        ):
            raise ValueError("provide a safe relative file path")
        if (self.material_id is None) != (self.expected_active_version is None):
            raise ValueError("replacement requires material and expected active version")
        if self.material_id is None and self.expected_revision is not None:
            raise ValueError("a metadata revision requires a replacement target")
        return self


class FolderCommand(FrozenModel):
    items: Annotated[tuple[FolderItem, ...], Field(min_length=1, max_length=100)]
    idempotency_key: IdempotencyKey

    @model_validator(mode="after")
    def distinct_paths(self) -> Self:
        if len({item.path for item in self.items}) != len(self.items):
            raise ValueError("folder paths must be distinct")
        return self


class FolderItemResult(FrozenModel):
    path: str
    outcome: Literal["submitted", "failed"]
    receipt: IngestReceipt | None = None
    code: ErrorCode | None = None
    message: str = ""


class FolderResult(Namespace):
    folder_id: UUID
    items: tuple[FolderItemResult, ...]


class FolderIngestion:
    def __init__(self, materials: MaterialService) -> None:
        self.materials, self.policy = materials, materials.policy

    async def submit(self, access: AccessContext, kg: UUID, command: FolderCommand) -> FolderResult:
        command = FolderCommand.model_validate(command)
        current = await self.policy.revalidate(access, action=Action.INGEST)
        if kg not in current.kg_ids:
            raise inaccessible_reference()
        if len(command.model_dump_json().encode()) > 1024 * 1024:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Folder manifest exceeds 1 MiB.")
        identifier = uuid5(
            current.org_id,
            digest(
                {
                    "kg": str(kg),
                    "issuer": current.principal.issuer,
                    "subject": current.principal.subject,
                    "key": command.idempotency_key,
                }
            ),
        )
        key = {"_id": f"{current.org_id}:{kg}:{identifier}"}
        fingerprint = digest(command.model_dump(mode="json"))
        saved = await self.materials.database.folder_commands.find_one_and_update(
            key,
            {
                "$setOnInsert": {
                    "org_id": str(current.org_id),
                    "kg_id": str(kg),
                    "digest": fingerprint,
                }
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        if saved is None or saved["digest"] != fingerprint:
            raise DomainError(ErrorCode.IDEMPOTENCY_CONFLICT, "Folder key has different input.")
        results = []
        for item in command.items:
            # Existing material commands own concurrent admission and idempotent replay.
            values = {
                "kg_id": kg,
                "title": item.path,
                "source": "folder:" + item.path,
                "upload_id": item.upload_id,
                "inline_text": item.inline_text,
                "idempotency_key": str(uuid5(identifier, item.path)),
            }
            try:
                receipt = (
                    await self.materials.add(current, AddMaterial.model_validate(values))
                    if item.material_id is None
                    else await self.materials.replace(
                        current,
                        ReplaceMaterial.model_validate(
                            values
                            | {
                                "material_id": item.material_id,
                                "expected_active_version": item.expected_active_version,
                                "expected_revision": item.expected_revision,
                            }
                        ),
                    )
                )
                results.append(
                    FolderItemResult(path=item.path, outcome="submitted", receipt=receipt)
                )
            except DomainError as error:
                results.append(
                    FolderItemResult(
                        path=item.path, outcome="failed", code=error.code, message=error.message
                    )
                )
            except Exception as error:
                # Log identities/type only: provider exceptions can contain private payloads.
                logger.error(
                    "Folder item failed: folder=%s kind=%s", identifier, type(error).__name__
                )
                results.append(
                    FolderItemResult(
                        path=item.path,
                        outcome="failed",
                        code=ErrorCode.BACKEND_UNAVAILABLE,
                        message="This file could not be submitted; replay the manifest to retry.",
                    )
                )
        await self.policy.revalidate(current)
        return FolderResult(
            org_id=current.org_id, kg_id=kg, folder_id=identifier, items=tuple(results)
        )
