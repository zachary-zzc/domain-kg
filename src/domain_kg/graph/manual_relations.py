"""Human relationship changes and claims without fabricated document evidence."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from domain_kg.graph.models import AssertionRecord, SupportRecord
from domain_kg.models import FrozenModel, ManualProvenance, NonNegative, ScopedRef, UTCDateTime


class RelationFields(FrozenModel):
    subject_id: UUID | None = None
    target_id: UUID | None = None
    predicate: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    polarity: Literal["positive", "negative", "uncertain"] | None = None
    description: Annotated[str, Field(max_length=8000)] | None = None
    confidence: Annotated[float, Field(ge=0, le=1)] | None = None
    tags: Annotated[tuple[str, ...], Field(max_length=100)] | None = None

    @model_validator(mode="after")
    def bounded_tags(self) -> Self:
        if self.tags is not None and any(not value or len(value) > 200 for value in self.tags):
            raise ValueError("tags require 1 to 200 characters")
        return self


class RelationChanges(RelationFields):
    @model_validator(mode="after")
    def nonempty_change(self) -> Self:
        if not self.model_fields_set or all(
            getattr(self, key) is None for key in self.model_fields_set
        ):
            raise ValueError("at least one typed change is required")
        return self


class ManualRelation(FrozenModel):
    curation: ScopedRef
    logical_assertion: ScopedRef
    fields: RelationFields
    fact: AssertionRecord | None = None
    support: SupportRecord | None = None
    provenance: ManualProvenance
    revision: NonNegative
    created_at: UTCDateTime
    public: bool = False
    material_ids: Annotated[tuple[UUID, ...], Field(max_length=100)] = ()

    @model_validator(mode="after")
    def independent_claim(self) -> Self:
        namespace = (self.curation.org_id, self.curation.kg_id)
        if (self.logical_assertion.org_id, self.logical_assertion.kg_id) != namespace:
            raise ValueError("manual relationship target must stay in its KG")
        if self.public == bool(self.material_ids) or len(set(self.material_ids)) != len(
            self.material_ids
        ):
            raise ValueError("choose source ownership or a source-less public override")
        if (self.fact is None) != (self.support is None):
            raise ValueError("manual claims require their exact independent support")
        if self.fact is not None and self.support is not None:
            if any(
                (ref.org_id, ref.kg_id) != namespace
                for ref in (
                    self.fact,
                    self.fact.subject,
                    self.fact.target,
                    self.support,
                    self.support.assertion,
                )
            ):
                raise ValueError("manual relationship references must stay in their namespace")
            if (
                self.support.status != "manual"
                or self.support.version is not None
                or self.support.spans
                or self.support.curation != self.curation
                or self.support.provenance != self.provenance
                or self.support.id != self.curation.id
                or self.support.assertion.id != self.fact.id
            ):
                raise ValueError("manual meaning cannot claim verified source spans")
        return self

    @property
    def text(self) -> str:
        claim = (
            f"{self.fact.subject.id} {self.fact.polarity} "
            f"{self.fact.predicate} {self.fact.target.id}"
            if self.fact
            else ""
        )
        return "\n".join(value for value in (self.fields.description, claim) if value)
