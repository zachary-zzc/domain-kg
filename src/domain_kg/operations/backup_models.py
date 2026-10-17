"""Backup envelopes and allowlisted authority sections, never database instructions."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from domain_kg.models import FrozenModel, Namespace, NonNegative, UTCDateTime
from domain_kg.operations.models import SnapshotPointer
from domain_kg.operations.packages import PackageObject

AuthorityTable = Literal[
    "kg_metadata",
    "kg_schemas",
    "import_plans",
    "published_view",
    "material_access",
    "curation_revisions",
    "curation_access",
    "curation_targets",
    "curation_scopes",
    "node_visibility",
    "jobs",
    "job_artifacts",
    "job_checkpoints",
    "operation_artifacts",
    "projection_attempts",
    "projection_record_refs",
    "publication_manifests",
    "publication_bindings",
    "import_source_origins",
    "materials",
    "material_versions",
    "entities",
    "assertions",
    "supports",
    "curations",
    "version_blobs",
    "source_blocks",
    "source_chunks",
    "observations",
    "engine_names",
    "import_id_mappings",
    "import_assertion_mappings",
]


class AuthoritySection(Namespace):
    backup_id: UUID
    area: Literal["control", "authority"]
    table: AuthorityTable
    records: Annotated[tuple[dict[str, JsonValue], ...], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def scoped_records(self) -> Self:
        def check(value: JsonValue) -> None:
            if isinstance(value, dict):
                if "org_id" in value and (value.get("org_id"), value.get("kg_id")) != (
                    str(self.org_id),
                    str(self.kg_id),
                ):
                    raise ValueError("authority references must stay in the backup namespace")
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)

        for row in self.records:
            if (row.get("org_id"), row.get("kg_id")) != (str(self.org_id), str(self.kg_id)):
                raise ValueError("authority records must have their exact namespace")
            check(row)
        return self


class BackupManifest(Namespace):
    schema_version: Literal[1] = 1
    backup_id: UUID
    job_id: UUID
    attempt_id: UUID
    snapshot: SnapshotPointer
    created_at: UTCDateTime
    expires_at: UTCDateTime
    source_count: NonNegative
    curation_count: NonNegative
    part_count: NonNegative
    pages: Annotated[tuple[PackageObject, ...], Field(min_length=1, max_length=10000)]

    @model_validator(mode="after")
    def exact_pages(self) -> Self:
        if self.expires_at <= self.created_at or (self.snapshot.org_id, self.snapshot.kg_id) != (
            self.org_id,
            self.kg_id,
        ):
            raise ValueError("backup boundary or retention differs")
        if len({page.id for page in self.pages}) != len(self.pages) or any(
            (page.org_id, page.kg_id, page.backup_id, page.attempt_id)
            != (self.org_id, self.kg_id, self.backup_id, self.attempt_id)
            for page in self.pages
        ):
            raise ValueError("backup pages require one exact operation attempt")
        return self


class BackupSeal(FrozenModel):
    manifest: PackageObject
    snapshot: SnapshotPointer
    job_id: UUID
    created_at: UTCDateTime
    expires_at: UTCDateTime

    @model_validator(mode="after")
    def exact_snapshot(self) -> Self:
        if (self.snapshot.org_id, self.snapshot.kg_id) != (
            self.manifest.org_id,
            self.manifest.kg_id,
        ) or self.expires_at <= self.created_at:
            raise ValueError("sealed backup ownership or retention differs")
        return self
