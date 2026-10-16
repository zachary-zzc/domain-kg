"""Explicit identity operations; identity and permissions come from verified context."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from domain_kg.graph.identity_models import IdentityGroup, IdentityState
from domain_kg.models import FrozenModel, IdempotencyKey, NonNegative


class IdentityChange(FrozenModel):
    kg_id: UUID
    entity_id: UUID
    action: Literal["merge", "split"]
    member_ids: Annotated[tuple[UUID, ...], Field(max_length=100)] = ()
    partitions: Annotated[tuple[IdentityGroup, ...], Field(max_length=100)] = ()
    expected_revision: NonNegative
    member_revisions: Annotated[tuple[tuple[UUID, NonNegative], ...], Field(max_length=99)] = ()
    curation_scope: Literal["source", "kg_public"] = "source"
    source_ids: Annotated[tuple[UUID, ...], Field(max_length=100)] = ()
    reason: Annotated[str, Field(min_length=1, max_length=4000)]
    dry_run: bool = False
    idempotency_key: IdempotencyKey

    @model_validator(mode="after")
    def complete_explicit_operation(self) -> Self:
        if self.curation_scope == "kg_public":
            if self.source_ids:
                raise ValueError("public identity has no source target list")
        elif not self.source_ids:
            raise ValueError("identity changes require explicit source ownership")
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("source owners must be distinct")
        if self.action == "merge":
            if (
                self.partitions
                or len(self.member_ids) < 2
                or len(set(self.member_ids)) != len(self.member_ids)
            ):
                raise ValueError("merge requires at least two distinct explicit origins")
        elif self.member_ids or len(self.partitions) < 2:
            raise ValueError("split requires at least two explicit disjoint partitions")
        state = self.state
        if self.entity_id not in state.members:
            raise ValueError("the primary original must belong to the selected identity state")
        supplied = [entity for entity, _ in self.member_revisions]
        if self.entity_id in supplied or len(set(supplied)) != len(supplied):
            raise ValueError("provide each other original revision exactly once")
        others = state.members - {self.entity_id}
        if not set(supplied).issubset(others) or (not self.dry_run and set(supplied) != others):
            raise ValueError("committing identity changes requires every original's revision")
        return self

    @property
    def state(self) -> IdentityState:
        return IdentityState(
            groups=(IdentityGroup(canonical_id=self.entity_id, member_ids=self.member_ids),)
            if self.action == "merge"
            else self.partitions
        )

    @property
    def members(self) -> frozenset[UUID]:
        return self.state.members

    @property
    def expected_targets(self) -> tuple[tuple[UUID, int], ...]:
        return tuple(
            sorted(
                ((self.entity_id, self.expected_revision), *self.member_revisions),
                key=lambda item: str(item[0]),
            )
        )


class IdentityImpact(FrozenModel):
    dry_run: Literal[True] = True
    action: Literal["merge", "split"]
    material_count: NonNegative
    member_ids: tuple[UUID, ...]
    member_revisions: tuple[tuple[UUID, NonNegative], ...]
    target_revision: NonNegative
    affected_chunks: NonNegative
    affected_observations: NonNegative
    affected_supports: NonNegative
