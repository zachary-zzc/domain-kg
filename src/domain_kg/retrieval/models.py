"""Typed filters and evidence. No database query language enters these contracts."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from domain_kg.graph.identity_models import IdentityWitness
from domain_kg.imports.provenance import ImportOrigin
from domain_kg.materials.models import MaterialMetadata
from domain_kg.models import FrozenModel, ManualProvenance, NonNegative, PreservedText, UTCDateTime

Mode = Literal["fast", "deep", "vector", "lexical", "graph"]
SupportStatus = Literal["verified", "manual", "imported_unverified"]


class SearchFilters(FrozenModel):
    @classmethod
    def for_curation(cls, material_ids: tuple[UUID, ...] = ()) -> Self:
        """Explicit curator commands inspect all current provenance under the same ACLs."""
        return cls(
            material_ids=material_ids,
            support_statuses=("verified", "manual", "imported_unverified"),
        )

    material_ids: tuple[UUID, ...] = ()
    exclude_material_ids: tuple[UUID, ...] = ()
    media_types: tuple[str, ...] = ()
    tags_all: tuple[str, ...] = ()
    tags_any: tuple[str, ...] = ()
    authors: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    entity_ids: tuple[UUID, ...] = ()
    exclude_entity_ids: tuple[UUID, ...] = ()
    entity_types: tuple[str, ...] = ()
    predicates: tuple[str, ...] = ()
    min_confidence: Annotated[float, Field(ge=0, le=1)] | None = None
    support_statuses: tuple[SupportStatus, ...] = ("verified", "manual")
    created_from: UTCDateTime | None = None
    created_before: UTCDateTime | None = None
    updated_from: UTCDateTime | None = None
    updated_before: UTCDateTime | None = None
    version_id: UUID | None = None

    @model_validator(mode="after")
    def valid_ranges(self) -> Self:
        for lower, upper in [
            (self.created_from, self.created_before),
            (self.updated_from, self.updated_before),
        ]:
            if lower is not None and upper is not None and lower >= upper:
                raise ValueError("time ranges must be nonempty and half-open")
        return self

    @property
    def requires_assertion_support(self) -> bool:
        return bool(
            self.entity_ids
            or self.entity_types
            or self.predicates
            or self.min_confidence is not None
        )

    @property
    def allows_sourceless_manual(self) -> bool:
        return "manual" in self.support_statuses and not any(
            (
                self.material_ids,
                self.exclude_material_ids,
                self.media_types,
                self.tags_all,
                self.tags_any,
                self.authors,
                self.sources,
                self.languages,
                self.created_from,
                self.created_before,
                self.updated_from,
                self.updated_before,
                self.version_id,
            )
        )

    def matches_material(self, metadata: MaterialMetadata) -> bool:
        if self.material_ids and metadata.material_id not in self.material_ids:
            return False
        if metadata.material_id in self.exclude_material_ids:
            return False
        if self.version_id is not None and metadata.version_id != self.version_id:
            return False
        for alternatives, value in [
            (self.media_types, metadata.media_type),
            (self.authors, metadata.author),
            (self.sources, metadata.source),
            (self.languages, metadata.language),
        ]:
            if alternatives and value not in alternatives:
                return False
        if not set(self.tags_all).issubset(metadata.tags):
            return False
        if self.tags_any and not set(self.tags_any).intersection(metadata.tags):
            return False
        for lower, upper, timestamp in [
            (self.created_from, self.created_before, metadata.created_at),
            (self.updated_from, self.updated_before, metadata.updated_at),
        ]:
            if (lower is not None and timestamp < lower) or (
                upper is not None and timestamp >= upper
            ):
                return False
        return True


class SearchRequest(FrozenModel):
    kg_ids: Annotated[tuple[UUID, ...], Field(min_length=1, max_length=10)]
    query: Annotated[str, Field(min_length=1, max_length=8000)]
    mode: Mode = "fast"
    filters: SearchFilters = SearchFilters()
    top_k: Annotated[int, Field(ge=1, le=50, strict=True)] = 10
    max_hops: Annotated[int, Field(ge=0, le=2, strict=True)] = 2
    max_nodes: Annotated[int, Field(ge=1, le=200, strict=True)] = 200
    max_edges: Annotated[int, Field(ge=0, le=400, strict=True)] = 400
    max_context_chars: Annotated[int, Field(ge=1000, le=48000, strict=True)] = 24000
    rerank: bool = False
    generate_keywords: bool = False
    allow_partial: bool = False

    @field_validator("query", mode="before")
    @classmethod
    def trim_query(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def mode_and_versions(self) -> Self:
        if self.mode != "deep" and (self.rerank or self.generate_keywords):
            raise ValueError("reranking and keyword generation require deep mode")
        if self.filters.version_id is not None and (
            len(self.kg_ids) != 1 or len(self.filters.material_ids) != 1
        ):
            raise ValueError("an explicit version requires one material in one KG")
        if len(set(self.kg_ids)) != len(self.kg_ids):
            raise ValueError("KG IDs must be distinct")
        return self

    @property
    def effective_hops(self) -> int:
        return self.max_hops if self.mode in ("deep", "graph") else 0


class SubgraphRequest(FrozenModel):
    kg_id: UUID
    seed_ids: Annotated[tuple[UUID, ...], Field(min_length=1)]
    max_hops: Annotated[int, Field(ge=0, le=2, strict=True)] = 2
    max_nodes: Annotated[int, Field(ge=1, le=200, strict=True)] = 200
    max_edges: Annotated[int, Field(ge=0, le=400, strict=True)] = 400
    filters: SearchFilters = SearchFilters()

    @model_validator(mode="after")
    def bounded_seeds(self) -> Self:
        if len(self.seed_ids) > self.max_nodes:
            raise ValueError("seed count exceeds the node cap")
        return self


class GraphStep(FrozenModel):
    subject_id: UUID
    assertion_id: UUID
    target_id: UUID
    support_id: UUID
    original_subject_id: UUID | None = None
    original_target_id: UUID | None = None
    identity_mappings: tuple[IdentityWitness, ...] = ()
    support_status: SupportStatus = "verified"
    import_origin: ImportOrigin | None = None

    @model_validator(mode="after")
    def honest_import_path(self) -> Self:
        if self.import_origin is not None and self.support_status != "imported_unverified":
            raise ValueError("imported path support cannot claim verified or human provenance")
        return self


class EvidenceCandidate(FrozenModel):
    kind: Literal["source", "manual"]
    kg_id: UUID
    text: PreservedText
    citation_ids: Annotated[tuple[UUID, ...], Field(min_length=1)]
    material_id: UUID | None = None
    version_id: UUID | None = None
    chunk_id: UUID | None = None
    curation_revision_id: UUID | None = None
    provenance: ManualProvenance | None = None
    entity_ids: tuple[UUID, ...] = ()
    assertion_ids: tuple[UUID, ...] = ()
    score: float = 0.0
    method_ranks: dict[str, Annotated[int, Field(ge=1)]] = Field(default_factory=dict)
    graph_paths: tuple[tuple[GraphStep, ...], ...] = ()
    identity_mappings: tuple[IdentityWitness, ...] = ()
    import_origin: ImportOrigin | None = None
    support_status: SupportStatus | None = None

    @model_validator(mode="after")
    def honest_provenance(self) -> Self:
        expected = (
            "imported_unverified"
            if self.import_origin
            else ("manual" if self.kind == "manual" else "verified")
        )
        if self.support_status is None:
            object.__setattr__(self, "support_status", expected)
        elif self.support_status != expected:
            raise ValueError("evidence status differs from its provenance")
        if self.import_origin is not None and (
            self.kind != "source"
            or (self.kg_id, self.material_id, self.version_id)
            != (
                self.import_origin.kg_id,
                self.import_origin.material_id,
                self.import_origin.version_id,
            )
        ):
            raise ValueError("import evidence must bind its exact source version")
        if any(witness.kg_id != self.kg_id for witness in self.identity_mappings):
            raise ValueError("identity witnesses must belong to the evidence KG")
        if self.kind == "source":
            if any(value is None for value in (self.material_id, self.version_id, self.chunk_id)):
                raise ValueError("source evidence requires material, version, and chunk IDs")
            if self.curation_revision_id is not None or self.provenance is not None:
                raise ValueError("source evidence cannot claim manual provenance")
        elif self.curation_revision_id is None or self.provenance is None:
            raise ValueError("manual evidence requires curation and actor/reason provenance")
        elif self.material_id is None and (
            self.version_id is not None or self.chunk_id is not None
        ):
            raise ValueError("source-less curation cannot invent version or chunk IDs")
        return self


class BranchStatus(FrozenModel):
    kg_id: UUID
    branch: Literal["vector", "lexical", "graph", "rerank", "keywords"]
    status: Literal["ok", "empty", "unavailable", "unsupported", "skipped"]


class SearchResponse(FrozenModel):
    request_id: UUID
    mode: Mode
    status: Literal["ok", "partial"]
    results: tuple[EvidenceCandidate, ...]
    applied_filters: SearchFilters
    kg_revisions: dict[UUID, NonNegative]
    policy_revision: NonNegative
    branch_status: tuple[BranchStatus, ...]
    projection_generations: dict[UUID, UUID]
    warnings: tuple[str, ...] = ()
    truncation: bool = False
