"""Explicit human identity partitions, separate from canonical source assertions."""

from typing import Annotated, Self
from uuid import UUID

from pydantic import Field, model_validator

from domain_kg.models import FrozenModel, ManualProvenance, NonNegative, ScopedRef, UTCDateTime


class IdentityGroup(FrozenModel):
    canonical_id: UUID
    member_ids: Annotated[tuple[UUID, ...], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def explicit_representative(self) -> Self:
        if self.canonical_id not in self.member_ids or len(set(self.member_ids)) != len(
            self.member_ids
        ):
            raise ValueError("a group requires distinct origins and its explicit representative")
        return self


class IdentityState(FrozenModel):
    groups: Annotated[tuple[IdentityGroup, ...], Field(max_length=100)] = ()

    @model_validator(mode="after")
    def disjoint_partition(self) -> Self:
        members = [value for group in self.groups for value in group.member_ids]
        if len(members) > 100 or len(set(members)) != len(members):
            raise ValueError("identity groups require at most 100 disjoint original IDs")
        return self

    @property
    def members(self) -> frozenset[UUID]:
        return frozenset(value for group in self.groups for value in group.member_ids)


class ManualIdentity(IdentityState):
    curation: ScopedRef
    target: ScopedRef
    provenance: ManualProvenance
    revision: NonNegative
    created_at: UTCDateTime
    public: bool = False
    material_ids: Annotated[tuple[UUID, ...], Field(max_length=100)] = ()

    @model_validator(mode="after")
    def independent_owner(self) -> Self:
        if (self.curation.org_id, self.curation.kg_id) != (self.target.org_id, self.target.kg_id):
            raise ValueError("identity targets must stay in their curation KG")
        if self.public == bool(self.material_ids) or len(set(self.material_ids)) != len(
            self.material_ids
        ):
            raise ValueError("choose explicit source ownership or an owner-public identity mapping")
        return self

    @property
    def text(self) -> str:
        # Labels remain independently sourced observations or explicit human fields.
        return ""
