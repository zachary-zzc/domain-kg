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
    SourceVersion,
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


class UploadCommand(FrozenModel):
    kg_id: UUID
    media_type: Annotated[str, Field(min_length=1, max_length=150)]
    sha256: SHA256
    size_bytes: Annotated[int, Field(ge=1, le=50 * 1024 * 1024, strict=True)]
    idempotency_key: IdempotencyKey
    target_material_id: UUID | None = None


class UploadReceipt(Namespace):
    upload_id: UUID
    sha256: SHA256
    size_bytes: NonNegative
    media_type: str
    expires_at: UTCDateTime


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


class MaterialSubmission(MaterialInput):
    kg_id: UUID
    title: Annotated[str, Field(min_length=1, max_length=1000)]
    idempotency_key: IdempotencyKey
    tags: tuple[str, ...] = ()
    author: Annotated[str, Field(max_length=1000)] | None = None
    source: Annotated[str, Field(max_length=4000)] | None = None
    language: Annotated[str, Field(max_length=50)] | None = None

    @model_validator(mode="after")
    def bounded_tags(self) -> Self:
        if len(self.tags) > 100 or any(not tag or len(tag) > 200 for tag in self.tags):
            raise ValueError("provide at most 100 tags of 1 to 200 characters")
        return self


class AddMaterial(MaterialSubmission):
    acl_mode: Literal["kg", "restricted"] = "kg"


class ReplaceMaterial(MaterialSubmission):
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


class RestoreMaterial(FrozenModel):
    kg_id: UUID
    material_id: UUID
    expected_revision: NonNegative
    idempotency_key: IdempotencyKey


class RestorationReceipt(Namespace):
    material_id: UUID
    revision: NonNegative
    state: Literal["restored", "pending"]
    job_id: UUID | None = None

    @model_validator(mode="after")
    def pending_requires_work(self) -> Self:
        if self.state == "pending" and self.job_id is None:
            raise ValueError("pending restoration requires a durable job")
        return self


class RestoreReadPermit(Namespace):
    """Internal pending-command proof; never exposed as a transport argument."""

    command_id: UUID
    source: SourceVersion
    expected_revision: NonNegative
    view_manifest_id: UUID
    projection_manifest_id: UUID
    generation: UUID
    visibility_revision: NonNegative

    @model_validator(mode="after")
    def source_namespace(self) -> Self:
        if (self.source.org_id, self.source.kg_id) != (self.org_id, self.kg_id):
            raise ValueError("restoration source must share its intent namespace")
        return self
