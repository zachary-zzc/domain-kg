"""Typed material snapshots retain evidence metadata and reset present access."""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import Field, ValidationError, field_validator, model_validator

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.materials.models import BlobRef, MaterialMetadata
from domain_kg.models import (
    FrozenModel,
    IdempotencyKey,
    Namespace,
    NonNegative,
    SourceVersion,
    UTCDateTime,
)
from domain_kg.operations.restore_documents import RestoreDocuments


class RestoredProposal(FrozenModel):
    kg_id: UUID
    title: Annotated[str, Field(min_length=1, max_length=1000)]
    idempotency_key: IdempotencyKey
    tags: Annotated[tuple[str, ...], Field(max_length=100)] = ()
    author: Annotated[str, Field(max_length=1000)] | None = None
    source: Annotated[str, Field(max_length=4000)] | None = None
    language: Annotated[str, Field(max_length=50)] | None = None
    acl_mode: Literal["kg", "restricted"] | None = None
    material_id: UUID | None = None
    expected_active_version: UUID | None = None

    @model_validator(mode="after")
    def paired_replacement(self) -> Self:
        if (self.material_id is None) != (self.expected_active_version is None):
            raise ValueError("replacement metadata requires its explicit predecessor")
        if any(not tag or len(tag) > 200 for tag in self.tags):
            raise ValueError("invalid material tags")
        return self


class RestoredMaterialAccess(Namespace):
    id: UUID
    acl_mode: Literal["restricted"] = "restricted"
    member_ids: tuple[UUID, ...] = ()
    group_ids: tuple[UUID, ...] = ()
    revision: NonNegative
    metadata_epoch: NonNegative
    active_version_id: UUID
    metadata: MaterialMetadata
    removed_at: UTCDateTime | None = None
    purge_after: UTCDateTime | None = None
    restored_at: UTCDateTime | None = None
    purged_at: UTCDateTime | None = None
    purged: bool = Field(default=False, strict=True)
    excluded: bool = Field(strict=True)
    tombstoned: bool = Field(strict=True)

    @field_validator("removed_at", "purge_after", "restored_at", "purged_at", mode="before")
    @classmethod
    def mongo_utc(cls, value: Any) -> Any:
        return (
            value.replace(tzinfo=UTC)
            if isinstance(value, datetime) and value.tzinfo is None
            else value
        )


class RestoredRetiredMaterialAccess(Namespace):
    """An identity/withdrawal fence, with no original metadata or usable version."""

    id: UUID
    acl_mode: Literal["restricted"] = "restricted"
    member_ids: tuple[UUID, ...] = ()
    group_ids: tuple[UUID, ...] = ()
    revision: NonNegative
    metadata_epoch: NonNegative = 0
    active_version_id: None = None
    purged: Literal[True] = True
    tombstoned: Literal[True] = True
    excluded: bool = Field(strict=True)
    purge_state: Literal["purged"] = "purged"
    purged_at: UTCDateTime


class RestoredVersionBlob(Namespace):
    id: UUID
    material_id: UUID
    blob: BlobRef
    state: Literal["accepted", "retained"]
    created_at: UTCDateTime
    updated_at: UTCDateTime | None = None
    proposed: RestoredProposal

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def mongo_utc(cls, value: Any) -> Any:
        return (
            value.replace(tzinfo=UTC)
            if isinstance(value, datetime) and value.tzinfo is None
            else value
        )

    @model_validator(mode="after")
    def exact_blob(self) -> Self:
        if (self.org_id, self.kg_id, self.material_id, self.id) != (
            self.blob.org_id,
            self.blob.kg_id,
            self.blob.material_id,
            self.blob.version_id,
        ) or self.proposed.kg_id != self.kg_id:
            raise ValueError("version metadata must retain its exact original source")
        return self


class RestoreMaterials:
    def __init__(self, documents: RestoreDocuments) -> None:
        self.documents, self.mapping = documents, documents.mapping

    @staticmethod
    def invalid() -> DomainError:
        return DomainError(
            ErrorCode.PROJECTION_INCONSISTENT, "Restored material references differ."
        )

    def retired(self, row: dict[str, Any]) -> RestoredRetiredMaterialAccess:
        if (
            (row.get("org_id"), row.get("kg_id"))
            != (str(self.mapping.origin.org_id), str(self.mapping.origin.kg_id))
            or row.get("purged") is not True
            or row.get("tombstoned") is not True
            or row.get("purge_state") != "purged"
            or row.get("active_version_id") is not None
            or "metadata" in row
        ):
            raise self.invalid()
        try:
            self.mapping._materials((UUID(row["id"]),))
            timestamp = row["purged_at"]
            if isinstance(timestamp, datetime) and timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            return RestoredRetiredMaterialAccess(
                **self.mapping.target.model_dump(),
                id=row["id"],
                revision=row["revision"],
                metadata_epoch=row.get("metadata_epoch", 0),
                excluded=row["excluded"],
                purged_at=timestamp,
            )
        except (ValidationError, ValueError, KeyError):
            raise self.invalid() from None

    def access(self, row: dict[str, Any], source: SourceVersion) -> RestoredMaterialAccess:
        target = self.mapping.source(source)
        if (row.get("org_id"), row.get("kg_id"), row.get("id")) != (
            str(source.org_id),
            str(source.kg_id),
            str(source.material_id),
        ):
            raise self.invalid()
        active = row.get("active_version_id")
        if active != str(source.version_id) and not (
            active is None and (row.get("excluded") is True or row.get("tombstoned") is True)
        ):
            raise self.invalid()
        try:
            metadata = MaterialMetadata.model_validate(row["metadata"])
            if (metadata.material_id, metadata.version_id) != (
                source.material_id,
                source.version_id,
            ):
                raise self.invalid()
            return RestoredMaterialAccess(
                **self.mapping.target.model_dump(),
                id=target.material_id,
                revision=row["revision"],
                metadata_epoch=row.get("metadata_epoch", 0),
                active_version_id=target.version_id,
                metadata=metadata,
                removed_at=row.get("removed_at"),
                purge_after=row.get("purge_after"),
                restored_at=row.get("restored_at"),
                purged_at=row.get("purged_at"),
                purged=row.get("purged", False),
                excluded=row["excluded"],
                tombstoned=row["tombstoned"],
            )
        except (ValidationError, KeyError):
            raise self.invalid() from None

    def version(self, row: dict[str, Any]) -> RestoredVersionBlob:
        try:
            stored = RestoredVersionBlob.model_validate(
                {key: row[key] for key in RestoredVersionBlob.model_fields if key in row}
            )
            original = self.documents.original(stored.blob)
            if stored.proposed.material_id is not None:
                if stored.proposed.material_id != stored.material_id:
                    raise self.invalid()
                predecessor = stored.proposed.expected_active_version
                if predecessor is None:
                    raise self.invalid()
                self.mapping.source(
                    SourceVersion(
                        org_id=stored.org_id,
                        kg_id=stored.kg_id,
                        material_id=stored.material_id,
                        version_id=predecessor,
                    )
                )
            proposal = RestoredProposal.model_validate(
                stored.proposed.model_dump()
                | {"kg_id": self.mapping.target.kg_id, "acl_mode": "restricted"}
            )
            return RestoredVersionBlob.model_validate(
                stored.model_dump()
                | self.mapping.target.model_dump()
                | {"blob": original, "proposed": proposal}
            )
        except (ValidationError, KeyError):
            raise self.invalid() from None
