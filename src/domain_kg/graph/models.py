"""Schema, canonical graph identities, and independent evidence ownership."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from domain_kg.ingestion.models import Span
from domain_kg.models import FrozenModel, ManualProvenance, Namespace, NonNegative, ScopedRef


class PredicateDefinition(FrozenModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    subject_types: tuple[str, ...]
    target_types: tuple[str, ...]


class DomainSchema(Namespace):
    id: UUID
    revision: NonNegative
    entity_types: tuple[str, ...]
    predicates: tuple[PredicateDefinition, ...]

    @model_validator(mode="after")
    def known_types(self) -> Self:
        if len(set(self.entity_types)) != len(self.entity_types):
            raise ValueError("entity types must be distinct")
        names = [predicate.name for predicate in self.predicates]
        if len(set(names)) != len(names):
            raise ValueError("predicates must be distinct")
        for predicate in self.predicates:
            if not set((*predicate.subject_types, *predicate.target_types)).issubset(
                self.entity_types
            ):
                raise ValueError("predicate endpoints must use defined entity types")
        return self


class EntityRecord(ScopedRef):
    """Stable identity only; evidence-derived display fields live on observations."""


class AssertionRecord(ScopedRef):
    subject: ScopedRef
    target: ScopedRef
    predicate: Annotated[str, Field(min_length=1, max_length=200)]
    polarity: Literal["positive", "negative", "uncertain"] = "positive"
    qualifiers: tuple[tuple[str, str], ...] = ()


class SupportRecord(ScopedRef):
    assertion: ScopedRef
    version: ScopedRef | None = None
    status: Literal["verified", "manual", "imported_unverified", "rejected"]
    spans: tuple[Span, ...] = ()
    curation: ScopedRef | None = None
    provenance: ManualProvenance | None = None
    confidence: Annotated[float, Field(ge=0, le=1)] | None = None

    @model_validator(mode="after")
    def supported_provenance(self) -> Self:
        if self.status == "verified" and (self.version is None or not self.spans):
            raise ValueError("verified supports require a source version and spans")
        if self.status == "manual" and (self.curation is None or self.provenance is None):
            raise ValueError("manual supports require durable curation and actor/reason")
        if self.status == "imported_unverified" and self.version is None:
            raise ValueError("imports require source ownership")
        return self
