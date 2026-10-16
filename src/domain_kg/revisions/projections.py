"""Sealed source/curation rows; display text is derived only from typed evidence."""

import hashlib
import json
from typing import Annotated, Any, Literal, Self
from uuid import UUID, uuid5

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from domain_kg.graph.curation_models import ManualNode
from domain_kg.graph.identity_models import ManualIdentity
from domain_kg.graph.manual_relations import ManualRelation
from domain_kg.imports.provenance import ImportOrigin
from domain_kg.ingestion.models import ParsedChunk
from domain_kg.ingestion.resolved import ResolvedAssertion, ResolvedEntity
from domain_kg.jobs.models import Checkpoint
from domain_kg.models import (
    SHA256,
    ManualProvenance,
    Namespace,
    PreservedText,
    ScopedRef,
    SourceVersion,
)
from domain_kg.providers.embeddings import EmbeddingProfile, normalized_vector


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


class ProjectionBody(Namespace):
    id: UUID
    source: SourceVersion | None = None
    curation: ScopedRef | None = None
    kind: Literal["chunk", "observation", "support", "curation"]
    chunk: ParsedChunk | None = None
    observation: ResolvedEntity | None = None
    assertion: ResolvedAssertion | None = None
    subject_observation: ResolvedEntity | None = None
    target_observation: ResolvedEntity | None = None
    manual_text: PreservedText | None = None
    provenance: ManualProvenance | None = None
    manual_node: ManualNode | None = None
    manual_relation: ManualRelation | None = None

    @model_validator(mode="after")
    def scoped_evidence(self) -> Self:
        if self.source is None and self.curation is None:
            raise ValueError("projection requires a source or curation owner")
        for reference in (self.source, self.curation):
            if reference and (reference.org_id, reference.kg_id) != (self.org_id, self.kg_id):
                raise ValueError("projection owner is outside its namespace")
        for item in (
            self.chunk,
            self.observation,
            self.subject_observation,
            self.target_observation,
        ):
            if item and (item.org_id, item.kg_id, item.material_id, item.version_id) != (
                self.org_id,
                self.kg_id,
                self.source.material_id if self.source else None,
                self.source.version_id if self.source else None,
            ):
                raise ValueError("projection evidence must belong to its source")
        expected = {
            "chunk": self.chunk,
            "observation": self.observation,
            "support": self.assertion,
            "curation": self.manual_text,
        }
        if expected[self.kind] is None or any(
            value is not None for key, value in expected.items() if key != self.kind
        ):
            raise ValueError("projection kind must bind its exact typed body")
        if self.kind == "curation":
            if (
                self.curation is None
                or not self.manual_text
                or self.provenance is None
                or self.id != self.curation.id
            ):
                raise ValueError("manual projections require durable curation provenance")
            if self.manual_node and (
                self.manual_node.curation != self.curation
                or self.manual_node.provenance != self.provenance
                or self.manual_node.text != self.manual_text
            ):
                raise ValueError("manual display fields must match their curation")
        elif self.source is None or self.curation is not None:
            raise ValueError("generated projections require independent source ownership")
        if self.manual_relation and (
            self.kind != "curation"
            or self.manual_node is not None
            or self.manual_relation.curation != self.curation
            or self.manual_relation.provenance != self.provenance
            or self.manual_relation.text != self.manual_text
        ):
            raise ValueError("manual relationship fields must match their curation")
        if self.manual_node and self.kind != "curation":
            raise ValueError("manual fields cannot become source observations")
        if self.chunk and self.chunk.id != self.id:
            raise ValueError("chunk identity differs")
        if self.observation and self.observation.id != self.id:
            raise ValueError("observation identity differs")
        if self.assertion:
            if (
                self.source is None
                or self.assertion.support.version
                != ScopedRef(org_id=self.org_id, kg_id=self.kg_id, id=self.source.version_id)
                or self.id != self.assertion.support.id
            ):
                raise ValueError("assertion support differs from projection ownership")
            if (
                self.subject_observation is None
                or self.target_observation is None
                or self.subject_observation.entity != self.assertion.assertion.subject
                or self.target_observation.entity != self.assertion.assertion.target
            ):
                raise ValueError("support display endpoints must share its source evidence")
        elif self.subject_observation is not None or self.target_observation is not None:
            raise ValueError("only supports have display endpoints")
        return self

    @property
    def claim(self) -> ResolvedAssertion | None:
        if self.assertion:
            return self.assertion
        manual = self.manual_relation
        if manual and manual.fact and manual.support:
            return ResolvedAssertion(assertion=manual.fact, support=manual.support)
        return None

    @property
    def text(self) -> str:
        if self.chunk:
            return self.chunk.text
        if self.observation:
            value = self.observation
            return "\n".join(
                filter(
                    None,
                    (
                        value.name,
                        value.entity_type,
                        value.description,
                        *value.aliases,
                        value.identifier,
                    ),
                )
            )
        if self.assertion and self.subject_observation and self.target_observation:
            fact = self.assertion.assertion
            return (
                f"{self.subject_observation.name} {fact.polarity} "
                f"{fact.predicate} {self.target_observation.name}"
            )
        return self.manual_text or ""


class ProjectionRow(ProjectionBody):
    vector: Annotated[tuple[float, ...], Field(min_length=1, max_length=4096)]

    def projection_id(self, attempt: UUID, manifest: UUID) -> UUID:
        return uuid5(self.org_id, f"{self.kg_id}/{attempt}/{manifest}/{self.kind}/{self.id}")

    def payload(
        self,
        *,
        attempt: UUID,
        manifest: UUID,
        generation: UUID,
        content_hash: str,
        profile: EmbeddingProfile,
    ) -> dict[str, Any]:
        value = self.model_dump(mode="json", exclude={"vector"})
        # Existing accepted source payloads predate manual node fields. Omitted
        # optional extensions must keep their sealed backend body/checksum stable.
        for extension in ("manual_node", "manual_relation"):
            if getattr(self, extension) is None:
                value.pop(extension, None)
        payload = {
            "org_id": str(self.org_id),
            "kg_id": str(self.kg_id),
            "generation": str(generation),
            "attempt_id": str(attempt),
            "manifest_id": str(manifest),
            "manifest_hash": content_hash,
            "projection_id": str(self.projection_id(attempt, manifest)),
            "logical_id": str(self.id),
            "kind": self.kind,
            "text": self.text,
            "body": json.dumps(value, sort_keys=True, ensure_ascii=False),
            "body_hash": digest(value),
            "embedding_profile_id": str(profile.id),
            "embedding_profile_revision": profile.revision,
            "lexical_key": self.projection_id(attempt, manifest).hex,
        }
        if profile.space_id is not None:
            payload["embedding_space_id"] = profile.space_id
        if self.source:
            payload.update(
                material_id=str(self.source.material_id),
                source_version_id=str(self.source.version_id),
            )
        if self.curation:
            payload["curation_revision_id"] = str(self.curation.id)
        if self.observation:
            payload["entity_id"] = str(self.observation.entity.id)
        if self.manual_node:
            payload["entity_id"] = str(self.manual_node.entity.id)
        if self.claim:
            fact, support = self.claim.assertion, self.claim.support
            payload.update(
                subject_id=str(fact.subject.id),
                target_id=str(fact.target.id),
                assertion_id=str(fact.id),
                support_status=support.status,
                predicate=fact.predicate,
                polarity=fact.polarity,
            )
            if support.confidence is not None:
                payload["confidence"] = support.confidence
        return payload


class EmbeddedDocument(SourceVersion):
    chunk_checkpoint: Checkpoint
    resolved_checkpoint: Checkpoint
    profile: EmbeddingProfile
    profile_selection_revision: int
    schema_id: UUID
    schema_revision: int
    rows: tuple[ProjectionRow, ...]
    warnings: tuple[str, ...] = ()
    import_origin: ImportOrigin | None = None

    @model_serializer(mode="wrap")
    def preserve_native_shape(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        result: dict[str, Any] = handler(self)
        if self.import_origin is None:
            result.pop("import_origin", None)
        return result

    @model_validator(mode="after")
    def sealed_rows(self) -> Self:
        owner = SourceVersion(
            org_id=self.org_id,
            kg_id=self.kg_id,
            material_id=self.material_id,
            version_id=self.version_id,
        )
        if self.import_origin is not None and not self.import_origin.matches(owner):
            raise ValueError("import provenance must bind this exact source version")
        if (self.profile.org_id, self.profile.kg_id) != (self.org_id, self.kg_id):
            raise ValueError("embedding profile namespace differs")
        for checkpoint in (self.chunk_checkpoint, self.resolved_checkpoint):
            if (checkpoint.org_id, checkpoint.kg_id) != (self.org_id, self.kg_id):
                raise ValueError("checkpoint namespace differs")
        if (
            self.chunk_checkpoint.stage != "chunking"
            or self.resolved_checkpoint.stage != "resolving"
        ):
            raise ValueError("incorrect source artifact stages")
        identities = [(row.kind, row.id) for row in self.rows]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate logical projection rows")
        for row in self.rows:
            if row.source != owner:
                raise ValueError("projection row source differs")
            if self.import_origin is not None and (
                row.curation is not None
                or row.assertion is not None
                and row.assertion.support.status != "imported_unverified"
            ):
                raise ValueError(
                    "imported rows cannot promote a claim to verified or human provenance"
                )
            normalized_vector(list(row.vector), self.profile.dimensions)
        return self


class CurationEmbeddedDocument(Namespace):
    curation: ScopedRef
    directive: ManualNode | ManualRelation | ManualIdentity
    profile: EmbeddingProfile
    profile_selection_revision: int
    schema_id: UUID
    schema_revision: int
    rows: Annotated[tuple[ProjectionRow, ...], Field(max_length=1)]
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def sealed_curation(self) -> Self:
        if (self.curation.org_id, self.curation.kg_id) != (self.org_id, self.kg_id) or (
            self.directive.curation != self.curation
        ):
            raise ValueError("manual document requires its canonical curation owner")
        if (self.profile.org_id, self.profile.kg_id) != (self.org_id, self.kg_id):
            raise ValueError("manual embedding profile namespace differs")
        if bool(self.rows) != bool(self.directive.text):
            raise ValueError("only embedded manual fields require projection rows")
        for row in self.rows:
            if (
                row.source is not None
                or row.curation != self.curation
                or (row.manual_node or row.manual_relation) != self.directive
            ):
                raise ValueError("manual rows cannot invent source ownership")
            normalized_vector(list(row.vector), self.profile.dimensions)
        return self


ProjectionDocument = EmbeddedDocument | CurationEmbeddedDocument


class AcceptedProjection(Namespace):
    source: SourceVersion | None = None
    curation: ScopedRef | None = None
    attempt_id: UUID
    manifest_id: UUID
    content_hash: SHA256
    generation: UUID
    chunk_artifact_id: UUID | None = None
    import_origin: ImportOrigin | None = None

    @model_serializer(mode="wrap")
    def preserve_native_shape(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        result: dict[str, Any] = handler(self)
        if self.import_origin is None:
            result.pop("import_origin", None)
        return result

    @model_validator(mode="after")
    def accepted_owner(self) -> Self:
        if self.import_origin is not None and (
            self.source is None or not self.import_origin.matches(self.source)
        ):
            raise ValueError("import provenance must bind an accepted source version")
        if self.source is None and self.curation is None:
            raise ValueError("accepted projection requires an owner")
        for owner in (self.source, self.curation):
            if owner and (owner.org_id, owner.kg_id) != (self.org_id, self.kg_id):
                raise ValueError("accepted owner namespace differs")
        if self.source and self.chunk_artifact_id is None:
            raise ValueError("source projection must bind exact parsed chunks")
        return self
