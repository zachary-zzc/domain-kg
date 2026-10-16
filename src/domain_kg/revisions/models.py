"""Immutable publication and curation contracts, independent of storage drivers."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from domain_kg.identity.models import AccessContext
from domain_kg.models import (
    SHA256,
    FrozenModel,
    IdempotencyKey,
    Namespace,
    NonNegative,
    ScopedRef,
    SourceVersion,
)
from domain_kg.retrieval.models import SearchFilters
from domain_kg.revisions.projections import AcceptedProjection

Backend = Literal["graph", "vector", "lexical"]


class ProjectionReceipt(Namespace):
    attempt_id: UUID
    generation: UUID
    manifest_id: UUID
    content_hash: SHA256
    backend: Backend
    record_count: NonNegative
    checksum: SHA256


class PublishedView(Namespace):
    revision: NonNegative
    generation: UUID
    manifest_id: UUID | None = None
    sources: tuple[SourceVersion, ...] = ()
    curations: tuple[ScopedRef, ...] = ()
    receipts: tuple[ProjectionReceipt, ...] = ()
    projections: tuple[AcceptedProjection, ...] = ()

    @model_validator(mode="after")
    def scoped_mappings(self) -> Self:
        for reference in (*self.sources, *self.curations, *self.receipts, *self.projections):
            if (reference.org_id, reference.kg_id) != (self.org_id, self.kg_id):
                raise ValueError("published mappings must share their manifest namespace")
        material_ids = [source.material_id for source in self.sources]
        if len(material_ids) != len(set(material_ids)):
            raise ValueError("a published view has one active version per material")
        bound_sources = [
            binding.source
            for binding in self.projections
            if binding.source and not binding.curation
        ]
        bound_curations = [binding.curation for binding in self.projections if binding.curation]
        if set(bound_sources) != set(self.sources) or set(bound_curations) != set(self.curations):
            raise ValueError("accepted sources and curations require exact projection bindings")
        if len(bound_sources) != len(self.sources) or len(bound_curations) != len(self.curations):
            raise ValueError("duplicate accepted projection owner")
        if any(binding.generation != self.generation for binding in self.projections):
            raise ValueError("projection binding generation differs from its view")
        return self


class ReadScope(FrozenModel):
    access: AccessContext
    view: PublishedView
    visible_sources: tuple[SourceVersion, ...] = ()
    visible_curations: tuple[ScopedRef, ...] = ()
    visibility_revision: NonNegative
    filters: SearchFilters = SearchFilters()

    @model_validator(mode="after")
    def accepted_and_authorized(self) -> Self:
        if self.access.org_id != self.view.org_id or self.view.kg_id not in self.access.kg_ids:
            raise ValueError("view is outside the resolved access context")
        if any(source not in self.view.sources for source in self.visible_sources):
            raise ValueError("visible sources must belong to the accepted view")
        if any(curation not in self.view.curations for curation in self.visible_curations):
            raise ValueError("visible curations must belong to the accepted view")
        return self


class RevisionBatch(Namespace):
    revision_id: UUID
    attempt_id: UUID
    generation: UUID
    manifest_id: UUID
    content_hash: SHA256
    expected_revision: NonNegative
    records: tuple[ScopedRef, ...]
    required_projections: tuple[Backend, ...] = ("graph", "vector", "lexical")

    @model_validator(mode="after")
    def scoped_records(self) -> Self:
        if any((ref.org_id, ref.kg_id) != (self.org_id, self.kg_id) for ref in self.records):
            raise ValueError("canonical records must share the revision namespace")
        if not self.required_projections or len(set(self.required_projections)) != len(
            self.required_projections
        ):
            raise ValueError("required projections must be nonempty and distinct")
        return self


class ReadinessReport(FrozenModel):
    batch: RevisionBatch
    receipts: tuple[ProjectionReceipt, ...]
    ready: bool
    issues: tuple[str, ...] = ()

    @model_validator(mode="after")
    def exact_attempt_receipts(self) -> Self:
        for receipt in self.receipts:
            for name in (
                "org_id",
                "kg_id",
                "attempt_id",
                "generation",
                "manifest_id",
                "content_hash",
            ):
                if getattr(receipt, name) != getattr(self.batch, name):
                    raise ValueError("receipt does not bind the exact attempt and manifest")
        backends = [receipt.backend for receipt in self.receipts]
        if len(backends) != len(set(backends)):
            raise ValueError("duplicate backend receipts")
        if self.ready and (self.issues or set(backends) != set(self.batch.required_projections)):
            raise ValueError("readiness requires all required projection receipts")
        return self


class PublishRevision(FrozenModel):
    report: ReadinessReport
    lease_token: UUID
    fence: NonNegative
    expected_revision: NonNegative
    expected_target_revisions: tuple[tuple[UUID, NonNegative], ...] = ()
    idempotency_key: IdempotencyKey

    @model_validator(mode="after")
    def ready_attempt_only(self) -> Self:
        if not self.report.ready or self.expected_revision != self.report.batch.expected_revision:
            raise ValueError("publication requires a ready report and the expected revision")
        return self


class RevisionReceipt(Namespace):
    revision_id: UUID
    state: Literal["pending", "published"]
    published_revision: NonNegative | None = None
    job_id: UUID | None = None

    @model_validator(mode="after")
    def explicit_acceptance(self) -> Self:
        if self.state == "pending" and (self.job_id is None or self.published_revision is not None):
            raise ValueError("pending revisions require a job, not a publication claim")
        if self.state == "published" and self.published_revision is None:
            raise ValueError("published revisions require the accepted KG revision")
        return self


class NodeChanges(FrozenModel):
    name: Annotated[str, Field(min_length=1, max_length=1000)] | None = None
    entity_type: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    description: Annotated[str, Field(max_length=8000)] | None = None
    aliases: tuple[str, ...] | None = None
    tags: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def nonempty_change(self) -> Self:
        if not self.model_fields_set or all(
            getattr(self, key) is None for key in self.model_fields_set
        ):
            raise ValueError("at least one typed change is required")
        return self


class RelationChanges(FrozenModel):
    predicate: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    description: Annotated[str, Field(max_length=8000)] | None = None
    target_id: UUID | None = None
    tags: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def nonempty_change(self) -> Self:
        if not self.model_fields_set or all(
            getattr(self, key) is None for key in self.model_fields_set
        ):
            raise ValueError("at least one typed change is required")
        return self


class EditCommand(FrozenModel):
    kg_id: UUID
    expected_revision: NonNegative
    curation_scope: Literal["source", "kg_public"] = "source"
    source_ids: tuple[UUID, ...] = ()
    reason: Annotated[str, Field(min_length=1, max_length=4000)]
    idempotency_key: IdempotencyKey


class NodeUpdate(EditCommand):
    entity_id: UUID
    changes: NodeChanges


class RelationUpdate(EditCommand):
    assertion_id: UUID
    changes: RelationChanges


class RollbackRevision(FrozenModel):
    kg_id: UUID
    revision_id: UUID
    expected_current_revision: NonNegative
    reason: Annotated[str, Field(min_length=1, max_length=4000)]
    idempotency_key: IdempotencyKey
