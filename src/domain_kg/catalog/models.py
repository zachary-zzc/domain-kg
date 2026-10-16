"""Placement uses operator-approved references, never caller-supplied credentials."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from domain_kg.models import FrozenModel, IdempotencyKey, Namespace, NonNegative


class BackendCapabilities(FrozenModel):
    backend_id: UUID
    kind: Literal["mongo", "neo4j", "qdrant", "s3"]
    healthy: bool
    capabilities: frozenset[str]


class StorageProfile(FrozenModel):
    id: UUID
    revision: NonNegative
    mongo_id: UUID
    neo4j_id: UUID
    qdrant_id: UUID
    object_store_id: UUID
    allowed_org_ids: frozenset[UUID]


class CreateKG(FrozenModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    domain_profile_id: UUID
    placement_id: UUID
    idempotency_key: IdempotencyKey


class KGReceipt(Namespace):
    state: Literal["provisioning", "ready", "read_only", "error", "archived", "purging", "purged"]
    revision: NonNegative
    job_id: UUID | None = None


class RegisterBackend(FrozenModel):
    kind: Literal["mongo", "neo4j", "qdrant", "s3"]
    secret_ref: UUID
