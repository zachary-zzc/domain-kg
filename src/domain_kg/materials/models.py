"""Material commands accept upload identities or text, never server paths."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from domain_kg.models import (
    SHA256,
    FrozenModel,
    IdempotencyKey,
    Namespace,
    NonNegative,
    PreservedText,
    UTCDateTime,
)


class BlobScope(Namespace):
    material_id: UUID
    version_id: UUID


class BlobRef(BlobScope):
    object_id: UUID
    sha256: SHA256
    size_bytes: Annotated[int, Field(ge=0, le=50 * 1024 * 1024, strict=True)]
    media_type: str


class MaterialMetadata(FrozenModel):
    material_id: UUID
    version_id: UUID
    media_type: str
    tags: tuple[str, ...] = ()
    author: str | None = None
    source: str | None = None
    language: str | None = None
    created_at: UTCDateTime
    updated_at: UTCDateTime


class MaterialInput(FrozenModel):
    upload_id: UUID | None = None
    inline_text: PreservedText | None = None

    @model_validator(mode="after")
    def one_source(self) -> Self:
        if (self.upload_id is None) == (self.inline_text is None):
            raise ValueError("provide exactly one upload ID or inline text")
        if self.inline_text is not None and (
            not self.inline_text or len(self.inline_text.encode("utf-8")) > 50 * 1024 * 1024
        ):
            raise ValueError("inline text must contain 1 to 50 MiB of UTF-8")
        return self


class AddMaterial(MaterialInput):
    kg_id: UUID
    title: Annotated[str, Field(min_length=1, max_length=1000)]
    idempotency_key: IdempotencyKey
    acl_mode: Literal["kg", "restricted"] = "kg"
    tags: tuple[str, ...] = ()


class ReplaceMaterial(AddMaterial):
    material_id: UUID
    expected_active_version: UUID


class RemoveMaterial(FrozenModel):
    kg_id: UUID
    material_id: UUID
    action: Literal["exclude", "delete"]
    expected_revision: NonNegative
    idempotency_key: IdempotencyKey
    dry_run: bool = False


class IngestReceipt(Namespace):
    material_id: UUID
    version_id: UUID
    job_id: UUID


class RemovalReceipt(Namespace):
    material_id: UUID
    revision: NonNegative
    dry_run: bool
    job_id: UUID | None = None
