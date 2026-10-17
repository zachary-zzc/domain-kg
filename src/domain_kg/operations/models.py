"""Owner operation intent and publication pointers contain no private connections."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from domain_kg.identity.models import Principal
from domain_kg.models import SHA256, FrozenModel, IdempotencyKey, Namespace, NonNegative, ScopedRef
from domain_kg.retrieval.models import SearchFilters

OwnerOperationKind = Literal["backup", "export", "restore"]


class BackupCommand(FrozenModel):
    kind: Literal["backup"] = "backup"
    kg_id: UUID
    expected_published_revision: NonNegative
    retention_days: Annotated[int, Field(ge=1, le=365, strict=True)] = 7
    idempotency_key: IdempotencyKey


class ExportCommand(FrozenModel):
    kind: Literal["export"] = "export"
    kg_id: UUID
    expected_published_revision: NonNegative
    format: Literal["jsonl", "csv", "graphml"] = "jsonl"
    filters: SearchFilters = SearchFilters()
    idempotency_key: IdempotencyKey


class RestoreCommand(FrozenModel):
    kind: Literal["restore"] = "restore"
    kg_id: UUID
    expected_published_revision: NonNegative
    backup_job_id: UUID
    placement_id: UUID
    provider_profile_id: UUID
    name: Annotated[str, Field(min_length=1, max_length=200)]
    idempotency_key: IdempotencyKey


OwnerCommand = Annotated[
    BackupCommand | ExportCommand | RestoreCommand, Field(discriminator="kind")
]


class SnapshotPointer(Namespace):
    revision: NonNegative
    generation: UUID
    manifest_id: UUID | None
    visibility_revision: NonNegative
    metadata_revision: NonNegative = 0
    configuration_epoch: NonNegative = 0

    @model_validator(mode="after")
    def accepted_pointer(self) -> Self:
        if (self.revision == 0) != (self.manifest_id is None):
            raise ValueError("a nonempty publication requires its accepted manifest")
        return self


class OwnerOperation(ScopedRef):
    job: ScopedRef
    actor: Principal
    command: OwnerCommand
    intent_hash: SHA256
    snapshot: SnapshotPointer

    @model_validator(mode="after")
    def exact_namespace(self) -> Self:
        if self.command.kg_id != self.kg_id or any(
            (ref.org_id, ref.kg_id) != (self.org_id, self.kg_id)
            for ref in (self.job, self.snapshot)
        ):
            raise ValueError("operation, job and publication must share their exact namespace")
        return self
