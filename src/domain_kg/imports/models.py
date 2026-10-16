"""Validated import plans reference completed uploads, never filesystem paths."""

from typing import Literal
from uuid import UUID

from domain_kg.models import SHA256, FrozenModel, IdempotencyKey, Namespace, NonNegative


class ImportSource(FrozenModel):
    kg_id: UUID
    upload_id: UUID
    format: Literal["csv", "json", "graphml", "lightrag"]
    idempotency_key: IdempotencyKey


class ImportPlan(Namespace):
    id: UUID
    source: ImportSource
    content_hash: SHA256
    entities: NonNegative
    assertions: NonNegative
    warnings: tuple[str, ...] = ()
    support_status: Literal["imported_unverified"] = "imported_unverified"
