"""Human-authored fields carry their own provenance, never invented source spans."""

from typing import Annotated, Self
from uuid import UUID

from pydantic import Field, model_validator

from domain_kg.models import FrozenModel, ManualProvenance, NonNegative, ScopedRef, UTCDateTime


class NodeFields(FrozenModel):
    name: Annotated[str, Field(min_length=1, max_length=1000)] | None = None
    entity_type: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    description: Annotated[str, Field(max_length=8000)] | None = None
    aliases: Annotated[tuple[str, ...], Field(max_length=30)] | None = None
    tags: Annotated[tuple[str, ...], Field(max_length=100)] | None = None

    @model_validator(mode="after")
    def bounded_values(self) -> Self:
        if self.aliases is not None and any(
            not value or len(value) > 1000 for value in self.aliases
        ):
            raise ValueError("aliases require 1 to 1000 characters")
        if self.tags is not None and any(not value or len(value) > 200 for value in self.tags):
            raise ValueError("tags require 1 to 200 characters")
        return self


class NodeChanges(NodeFields):
    @model_validator(mode="after")
    def nonempty_change(self) -> Self:
        if not self.model_fields_set or all(
            getattr(self, key) is None for key in self.model_fields_set
        ):
            raise ValueError("at least one typed change is required")
        return self


class ManualNode(FrozenModel):
    curation: ScopedRef
    entity: ScopedRef
    changes: NodeFields
    provenance: ManualProvenance
    revision: NonNegative
    created_at: UTCDateTime
    public: bool = False
    material_ids: Annotated[tuple[UUID, ...], Field(max_length=100)] = ()

    @model_validator(mode="after")
    def independent_owner(self) -> Self:
        if (self.curation.org_id, self.curation.kg_id) != (self.entity.org_id, self.entity.kg_id):
            raise ValueError("manual fields must stay in one KG")
        if self.public == bool(self.material_ids) or len(set(self.material_ids)) != len(
            self.material_ids
        ):
            raise ValueError("choose explicit source ownership or a source-less public override")
        return self

    @property
    def text(self) -> str:
        return "\n".join(
            value
            for value in (
                self.changes.name,
                self.changes.entity_type,
                self.changes.description,
                *(self.changes.aliases or ()),
            )
            if value
        )
