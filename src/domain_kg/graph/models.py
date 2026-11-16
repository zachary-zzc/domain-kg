"""Schema, canonical graph identities, and independent evidence ownership."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from domain_kg.ingestion.models import Span, span_status
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
    # Omit the historical default so sealed legacy artifacts keep their hashes.
    vocabulary: Literal["closed", "open"] = Field(
        default="closed", exclude_if=lambda value: value == "closed"
    )

    def allows_entity_type(self, value: str) -> bool:
        return self._label(value) and (self.vocabulary == "open" or value in self.entity_types)

    def allows_predicate(self, value: str) -> bool:
        return self._label(value) and (
            self.vocabulary == "open" or any(p.name == value for p in self.predicates)
        )

    def allows_relation(self, predicate: str, subject_type: str, target_type: str) -> bool:
        if not (
            self.allows_predicate(predicate)
            and self.allows_entity_type(subject_type)
            and self.allows_entity_type(target_type)
        ):
            return False
        return self.vocabulary == "open" or any(
            p.name == predicate
            and subject_type in p.subject_types
            and target_type in p.target_types
            for p in self.predicates
        )

    @staticmethod
    def _label(value: str) -> bool:
        # Labels are bounded property values, never Cypher labels or instructions.
        return bool(value.strip()) and len(value) <= 200 and value.isprintable()

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
    status: Literal["verified", "model_interpreted", "manual", "imported_unverified", "rejected"]
    spans: tuple[Span, ...] = ()
    curation: ScopedRef | None = None
    provenance: ManualProvenance | None = None
    confidence: Annotated[float, Field(ge=0, le=1)] | None = None

    @model_validator(mode="after")
    def supported_provenance(self) -> Self:
        if self.status in ("verified", "model_interpreted") and (
            self.version is None or not self.spans
        ):
            raise ValueError("verified supports require a source version and spans")
        if self.status in ("verified", "model_interpreted") and self.status != span_status(
            self.spans
        ):
            raise ValueError("support status differs from image provenance")
        if self.status == "manual" and (self.curation is None or self.provenance is None):
            raise ValueError("manual supports require durable curation and actor/reason")
        if self.status == "imported_unverified" and self.version is None:
            raise ValueError("imports require source ownership")
        return self
