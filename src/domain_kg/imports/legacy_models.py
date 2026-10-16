"""Bounded source-qualified legacy observations; all claims remain unverified."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from domain_kg.models import SHA256, FrozenModel, Namespace, PreservedText

LegacyID = Annotated[PreservedText, Field(min_length=1, max_length=1000)]


class LegacyEntity(FrozenModel):
    id: UUID
    entity_id: UUID
    legacy_id: LegacyID
    source_id: LegacyID
    name: LegacyID
    entity_type: Annotated[str, Field(min_length=1, max_length=200)]


class LegacyRelation(FrozenModel):
    id: UUID
    assertion_id: UUID
    legacy_id: LegacyID
    subject_id: UUID
    target_id: UUID
    source_id: LegacyID
    predicate: Annotated[str, Field(min_length=1, max_length=200)]
    status: Literal["imported_unverified"] = "imported_unverified"


class LegacyGraph(FrozenModel):
    namespace: Namespace
    origin: LegacyID
    raw_sha256: SHA256
    normalized_sha256: SHA256
    normalization_version: str = "legacy-nfc-newlines-sha256-v1"
    entities: Annotated[tuple[LegacyEntity, ...], Field(max_length=2000)]
    relations: Annotated[tuple[LegacyRelation, ...], Field(max_length=1000)]
    warnings: tuple[str, ...] = ()
    originals_verified: Literal[False] = False


class ImportNode(FrozenModel):
    id: LegacyID
    name: LegacyID
    entity_type: Annotated[str, Field(min_length=1, max_length=200)] = "concept"
    description: Annotated[str, Field(max_length=4000)] = ""


class ImportEdge(FrozenModel):
    id: LegacyID
    source: LegacyID
    target: LegacyID
    predicate: Annotated[str, Field(min_length=1, max_length=200)]
    source_ids: Annotated[tuple[LegacyID, ...], Field(min_length=1, max_length=100)]
