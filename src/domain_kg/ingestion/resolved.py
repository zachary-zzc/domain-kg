"""Prepared source observations and independently owned assertion supports."""

from typing import Self
from uuid import UUID

from pydantic import model_validator

from domain_kg.graph.models import AssertionRecord, DomainSchema, SupportRecord
from domain_kg.ingestion.models import ExtractionBatch, Span
from domain_kg.models import FrozenModel, NonNegative, ScopedRef, SourceVersion


class ResolvedEntity(SourceVersion):
    id: UUID
    chunk_id: UUID
    entity: ScopedRef
    name: str
    entity_type: str
    description: str
    aliases: tuple[str, ...] = ()
    identifier: str | None = None
    spans: tuple[Span, ...]

    @model_validator(mode="after")
    def scoped_entity(self) -> Self:
        if (self.entity.org_id, self.entity.kg_id) != (self.org_id, self.kg_id):
            raise ValueError("observation entity must share its source namespace")
        return self


class ExtractionArtifact(SourceVersion):
    profile_id: UUID
    profile_revision: NonNegative
    selection_revision: NonNegative
    domain_schema: DomainSchema
    chunk_artifact_id: UUID
    batches: tuple[ExtractionBatch, ...]

    @model_validator(mode="after")
    def scoped_batches(self) -> Self:
        if (self.domain_schema.org_id, self.domain_schema.kg_id) != (self.org_id, self.kg_id):
            raise ValueError("schema must share source namespace")
        for batch in self.batches:
            if (batch.org_id, batch.kg_id, batch.material_id, batch.version_id) != (
                self.org_id,
                self.kg_id,
                self.material_id,
                self.version_id,
            ) or batch.provider_profile_id != self.profile_id:
                raise ValueError("extractions must share source and profile")
            if (batch.schema_id, batch.schema_revision) != (
                self.domain_schema.id,
                self.domain_schema.revision,
            ):
                raise ValueError("extractions must use this exact schema")
        if len({batch.chunk_id for batch in self.batches}) != len(self.batches):
            raise ValueError("duplicate source chunks")
        return self


class ResolvedDocument(SourceVersion):
    extraction: ExtractionArtifact
    batches: tuple["ResolvedBatch", ...]

    @model_validator(mode="after")
    def scoped_result(self) -> Self:
        for item in (self.extraction, *self.batches):
            if (item.org_id, item.kg_id, item.material_id, item.version_id) != (
                self.org_id,
                self.kg_id,
                self.material_id,
                self.version_id,
            ):
                raise ValueError("resolved document must share source namespace")
        if len({batch.extraction_id for batch in self.batches}) != len(self.batches):
            raise ValueError("duplicate extraction results")
        return self


class ResolvedAssertion(FrozenModel):
    assertion: AssertionRecord
    support: SupportRecord
    subject_observation_id: UUID | None = None
    target_observation_id: UUID | None = None

    @model_validator(mode="after")
    def assertion_support(self) -> Self:
        if self.support.assertion != ScopedRef(
            org_id=self.assertion.org_id, kg_id=self.assertion.kg_id, id=self.assertion.id
        ):
            raise ValueError("support must bind its exact scoped assertion")
        return self


class ResolvedBatch(SourceVersion):
    extraction_id: UUID
    chunk_id: UUID
    schema_id: UUID
    schema_revision: NonNegative
    provider_profile_id: UUID
    entities: tuple[ResolvedEntity, ...]
    assertions: tuple[ResolvedAssertion, ...]
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def source_owned(self) -> Self:
        for entity in self.entities:
            if (
                entity.org_id,
                entity.kg_id,
                entity.material_id,
                entity.version_id,
                entity.chunk_id,
            ) != (self.org_id, self.kg_id, self.material_id, self.version_id, self.chunk_id):
                raise ValueError("observation must belong to this source")
        for item in self.assertions:
            if item.support.version != ScopedRef(
                org_id=self.org_id, kg_id=self.kg_id, id=self.version_id
            ):
                raise ValueError("support must retain this source version")
        return self
