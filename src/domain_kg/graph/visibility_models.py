"""Human visibility actions and current masks are independent of projection rows."""

from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from domain_kg.graph.identity_commands import IdentityChange
from domain_kg.graph.identity_models import IdentityGroup
from domain_kg.models import (
    FrozenModel,
    IdempotencyKey,
    Namespace,
    NonNegative,
    ScopedRef,
)


class CurationCommand(FrozenModel):
    kg_id: UUID
    entity_id: UUID
    action: Literal["suppress", "restore", "merge", "split"]
    expected_revision: NonNegative
    curation_scope: Literal["source", "kg_public"] = "source"
    source_ids: Annotated[tuple[UUID, ...], Field(max_length=100)] = ()
    reason: Annotated[str, Field(min_length=1, max_length=4000)]
    dry_run: bool = False
    idempotency_key: IdempotencyKey
    member_ids: Annotated[tuple[UUID, ...], Field(max_length=100)] = ()
    partitions: Annotated[tuple[IdentityGroup, ...], Field(max_length=100)] = ()
    member_revisions: Annotated[tuple[tuple[UUID, NonNegative], ...], Field(max_length=99)] = ()

    @model_validator(mode="after")
    def explicit_scope(self) -> Self:
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("source targets must be distinct")
        if self.curation_scope == "kg_public" and self.source_ids:
            raise ValueError("a public node fence has no source target list")
        if self.action in ("merge", "split"):
            IdentityChange.model_validate(self.model_dump())
        elif self.member_ids or self.partitions or self.member_revisions:
            raise ValueError("visibility actions do not accept identity group arguments")
        return self

    @model_serializer(mode="wrap")
    def preserve_visibility_input(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        result: dict[str, Any] = handler(self)
        if self.action in ("suppress", "restore"):
            for key in ("member_ids", "partitions", "member_revisions"):
                result.pop(key, None)
        return result


class CurationImpact(FrozenModel):
    dry_run: Literal[True] = True
    material_count: NonNegative
    affected_chunks: NonNegative
    affected_observations: NonNegative
    affected_supports: NonNegative
    target_revision: NonNegative


class CurationReceipt(Namespace):
    revision_id: UUID
    target_id: UUID
    action: Literal["suppress", "restore"]
    state: Literal["applied"] = "applied"
    target_revision: NonNegative
    visibility_revision: NonNegative
    job_id: UUID | None = None


class IdentityFenceGroup(FrozenModel):
    material_id: UUID | None = None
    member_ids: Annotated[tuple[UUID, ...], Field(min_length=1, max_length=100)]


class NodeFence(Namespace):
    entity_id: UUID
    curation: ScopedRef
    revision: Annotated[int, Field(ge=1, strict=True)]
    public: bool = False
    material_ids: Annotated[tuple[UUID, ...], Field(max_length=100)] = ()
    identity_groups: Annotated[tuple[IdentityFenceGroup, ...], Field(max_length=101)] = ()

    @model_validator(mode="after")
    def scoped_mask(self) -> Self:
        if (self.curation.org_id, self.curation.kg_id) != (self.org_id, self.kg_id):
            raise ValueError("node fence curation must share its namespace")
        if self.public == bool(self.material_ids) or len(set(self.material_ids)) != len(
            self.material_ids
        ):
            raise ValueError("choose source ownership or an explicit public fence")
        contexts = [item.material_id for item in self.identity_groups]
        members = {member for item in self.identity_groups for member in item.member_ids}
        if (
            len(set(contexts)) != len(contexts)
            or len(members) > 100
            or any(
                len(set(item.member_ids)) != len(item.member_ids) for item in self.identity_groups
            )
            or not self.public
            and any(context not in self.material_ids for context in contexts)
        ):
            raise ValueError("identity fence snapshots require bounded, distinct owned contexts")
        return self
