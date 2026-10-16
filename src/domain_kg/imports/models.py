"""Validated import plans reference completed uploads, never filesystem paths."""

from typing import Literal
from uuid import UUID

from domain_kg.imports.legacy_models import LegacyID
from domain_kg.models import (
    SHA256,
    FrozenModel,
    IdempotencyKey,
    Namespace,
    NonNegative,
    UTCDateTime,
)


class ImportSource(FrozenModel):
    kg_id: UUID
    upload_id: UUID
    format: Literal["csv", "json", "graphml", "lightrag"]
    idempotency_key: IdempotencyKey
    origin: LegacyID | None = None


class ImportPlan(Namespace):
    id: UUID
    source: ImportSource
    content_hash: SHA256
    entities: NonNegative
    assertions: NonNegative
    warnings: tuple[str, ...] = ()
    support_status: Literal["imported_unverified"] = "imported_unverified"
    policy_revision: NonNegative = 0
    published_revision: NonNegative = 0
    kg_revision: NonNegative = 0
    visibility_revision: NonNegative = 0
    schema_id: UUID | None = None
    schema_revision: NonNegative = 0
    expires_at: UTCDateTime | None = None
