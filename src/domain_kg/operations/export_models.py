"""Export files retain only scoped graph evidence and a current read boundary."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from domain_kg.models import SHA256, Namespace, NonNegative, ScopedRef, UTCDateTime
from domain_kg.operations.export_formats import ExportFormat
from domain_kg.operations.models import SnapshotPointer
from domain_kg.operations.packages import PackageObject
from domain_kg.operations.storage import PackageStore


class ExportObject(ScopedRef):
    export_id: UUID
    attempt_id: UUID
    sha256: SHA256
    size_bytes: Annotated[int, Field(ge=1, le=50 * 1024 * 1024, strict=True)]
    media_type: str

    @property
    def retained(self) -> PackageObject:
        # Reuse the bounded immutable object I/O without pretending to own a source file.
        value = self.model_dump(mode="json")
        value["backup_id"] = value.pop("export_id")
        return PackageObject.model_validate(value)


class ExportStore(PackageStore):
    def _key(self, reference: PackageObject) -> str:
        return "retained-exports/" + super()._key(reference).removeprefix("retained-backups/")


class ExportManifest(Namespace):
    schema_version: Literal[1] = 1
    export_id: UUID
    job_id: UUID
    attempt_id: UUID
    snapshot: SnapshotPointer
    policy_revision: NonNegative
    filters_hash: SHA256
    format: ExportFormat
    node_count: Annotated[int, Field(ge=0, le=1000, strict=True)]
    edge_count: Annotated[int, Field(ge=0, le=5000, strict=True)]
    created_at: UTCDateTime
    expires_at: UTCDateTime
    content: ExportObject

    @model_validator(mode="after")
    def exact_owner(self) -> Self:
        if self.expires_at <= self.created_at or (
            self.content.org_id,
            self.content.kg_id,
            self.content.export_id,
            self.content.attempt_id,
            self.snapshot.org_id,
            self.snapshot.kg_id,
        ) != (self.org_id, self.kg_id, self.export_id, self.attempt_id, self.org_id, self.kg_id):
            raise ValueError("export content must bind its exact operation and snapshot")
        return self
