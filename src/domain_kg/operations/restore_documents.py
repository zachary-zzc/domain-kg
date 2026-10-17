"""Pure typed source-document transforms; callers reseal and authorize all writes."""

from collections.abc import Callable

from pydantic import ValidationError

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.curation_models import ManualNode
from domain_kg.graph.identity_models import ManualIdentity
from domain_kg.graph.manual_relations import ManualRelation
from domain_kg.graph.models import DomainSchema
from domain_kg.imports.legacy_models import LegacyGraph
from domain_kg.imports.prepared import PreparedImport
from domain_kg.imports.provenance import ImportOrigin
from domain_kg.ingestion.models import ExtractionBatch, ParsedBlock, ParsedChunk, ParsedDocument
from domain_kg.ingestion.resolved import (
    ExtractionArtifact,
    ResolvedAssertion,
    ResolvedBatch,
    ResolvedDocument,
)
from domain_kg.jobs.models import Checkpoint
from domain_kg.materials.models import BlobRef
from domain_kg.models import SourceVersion
from domain_kg.operations.remapping import RestoreRemapper
from domain_kg.operations.restore_imports import RestoredImport, checksum
from domain_kg.providers.embeddings import EmbeddingProfile
from domain_kg.revisions.projections import (
    CurationEmbeddedDocument,
    EmbeddedDocument,
    ProjectionRow,
)


class RestoreDocuments:
    def __init__(
        self,
        mapping: RestoreRemapper,
        *,
        originals: tuple[BlobRef, ...],
        imports: tuple[RestoredImport, ...] = (),
    ) -> None:
        self.mapping = mapping
        if (
            len(imports) > 10000
            or sum(len(i.graph.entities) + len(i.graph.relations) for i in imports) > 100000
        ):
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Restore import inventory is too large.")
        self.imports = {item.original: RestoredImport.model_validate(item) for item in imports}
        if len(self.imports) != len(imports):
            raise inaccessible_reference()
        self.import_sources = {
            self.owner_from_origin(item.original): item for item in self.imports.values()
        }
        if len(self.import_sources) != len(self.imports):
            raise inaccessible_reference()
        for item in self.imports.values():
            if mapping.source(item.original) != RestoreDocuments.owner_from_origin(item.origin):
                raise inaccessible_reference()
        if len(originals) > 10000:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Restore source inventory is too large.")
        self.originals = {item.version_id: item for item in originals}
        if len(self.originals) != len(originals):
            raise inaccessible_reference()
        for original in originals:
            mapping.source(self.owner(original))

    @staticmethod
    def owner(blob: BlobRef) -> SourceVersion:
        return SourceVersion(
            org_id=blob.org_id,
            kg_id=blob.kg_id,
            material_id=blob.material_id,
            version_id=blob.version_id,
        )

    @staticmethod
    def owner_from_origin(origin: ImportOrigin) -> SourceVersion:
        return SourceVersion(**{k: getattr(origin, k) for k in SourceVersion.model_fields})

    def import_origin(self, origin: ImportOrigin) -> ImportOrigin:
        result = self.imports.get(origin)
        if result is None:
            raise self.inconsistent()
        return result.origin

    def legacy(self, graph: LegacyGraph, owner: SourceVersion) -> LegacyGraph:
        self.mapping.source(owner)
        bundle = self.import_sources.get(owner)
        if bundle is None or checksum(graph) != bundle.original.graph_hash:
            raise self.inconsistent()
        return bundle.graph

    def prepared(
        self, item: PreparedImport, checkpoint: Callable[[Checkpoint], Checkpoint]
    ) -> PreparedImport:
        try:
            item = PreparedImport.model_validate(item)
            rewritten = checkpoint(item.chunk_checkpoint)
            old = item.chunk_checkpoint
            if (
                rewritten.org_id,
                rewritten.kg_id,
                rewritten.job_id,
                rewritten.stage,
                rewritten.artifact_id,
            ) != (
                self.mapping.target.org_id,
                self.mapping.target.kg_id,
                old.job_id,
                old.stage,
                old.artifact_id,
            ):
                raise self.inconsistent()
            return PreparedImport.model_validate(
                item.model_dump()
                | self.mapping.source(item).model_dump()
                | {
                    "import_origin": self.import_origin(item.import_origin),
                    "domain_schema": self.schema(item.domain_schema),
                    "chunk_checkpoint": rewritten,
                    "rows": tuple(self.mapping.projection_body(row) for row in item.rows),
                }
            )
        except ValidationError:
            raise self.inconsistent() from None

    def original(self, blob: BlobRef) -> BlobRef:
        source = self.mapping.source(self.owner(blob))
        if self.originals.get(blob.version_id) != blob:
            raise inaccessible_reference()
        return BlobRef.model_validate(blob.model_dump() | source.model_dump())

    def parsed(self, item: ParsedDocument) -> ParsedDocument:
        try:
            item = ParsedDocument.model_validate(item)
            return ParsedDocument.model_validate(
                item.model_dump()
                | {
                    "blob": self.original(item.blob),
                    "blocks": tuple(
                        ParsedBlock.model_validate(
                            block.model_dump() | self.mapping.source(block).model_dump()
                        )
                        for block in item.blocks
                    ),
                    "chunks": tuple(
                        ParsedChunk.model_validate(
                            chunk.model_dump() | self.mapping.source(chunk).model_dump()
                        )
                        for chunk in item.chunks
                    ),
                }
            )
        except ValidationError:
            raise self.inconsistent() from None

    @staticmethod
    def inconsistent() -> DomainError:
        return DomainError(
            ErrorCode.PROJECTION_INCONSISTENT, "Restore source artifact is inconsistent."
        )

    def schema(self, schema: DomainSchema) -> DomainSchema:
        if (schema.org_id, schema.kg_id) != (self.mapping.origin.org_id, self.mapping.origin.kg_id):
            raise inaccessible_reference()
        return DomainSchema.model_validate(schema.model_dump() | self.mapping.target.model_dump())

    def extraction(self, item: ExtractionArtifact) -> ExtractionArtifact:
        try:
            item = ExtractionArtifact.model_validate(item)
            return ExtractionArtifact.model_validate(
                item.model_dump()
                | self.mapping.source(item).model_dump()
                | {
                    "domain_schema": self.schema(item.domain_schema),
                    "batches": tuple(
                        ExtractionBatch.model_validate(
                            batch.model_dump() | self.mapping.source(batch).model_dump()
                        )
                        for batch in item.batches
                    ),
                }
            )
        except ValidationError:
            raise self.inconsistent() from None

    def resolved(self, item: ResolvedDocument) -> ResolvedDocument:
        try:
            item = ResolvedDocument.model_validate(item)
            schema = item.extraction.domain_schema
            batches = []
            for batch in item.batches:
                if (batch.schema_id, batch.schema_revision, batch.provider_profile_id) != (
                    schema.id,
                    schema.revision,
                    item.extraction.profile_id,
                ):
                    raise self.inconsistent()
                batches.append(
                    ResolvedBatch.model_validate(
                        batch.model_dump()
                        | self.mapping.source(batch).model_dump()
                        | {
                            "entities": tuple(
                                self.mapping.observation(entity) for entity in batch.entities
                            ),
                            "assertions": tuple(
                                ResolvedAssertion.model_validate(
                                    claim.model_dump()
                                    | {
                                        "assertion": self.mapping.assertion(claim.assertion),
                                        "support": self.mapping.support(claim.support),
                                    }
                                )
                                for claim in batch.assertions
                            ),
                        }
                    )
                )
            return ResolvedDocument.model_validate(
                item.model_dump()
                | self.mapping.source(item).model_dump()
                | {
                    "extraction": self.extraction(item.extraction),
                    "batches": tuple(batches),
                }
            )
        except ValidationError:
            raise self.inconsistent() from None

    def _projection_rows(
        self,
        original: tuple[ProjectionRow, ...],
        old_profile: EmbeddingProfile,
        profile: EmbeddingProfile,
        vectors: tuple[tuple[float, ...], ...] | None,
    ) -> tuple[ProjectionRow, ...]:
        if vectors is not None and len(vectors) != len(original):
            raise self.inconsistent()
        compatible = (
            old_profile.space_id is not None
            and old_profile.space_id == profile.space_id
            and old_profile.model == profile.model
            and old_profile.dimensions == profile.dimensions
        )
        rows = []
        for index, row in enumerate(original):
            body = self.mapping.projection_body(row)
            if vectors is None and (not compatible or body.text != row.text):
                raise DomainError(
                    ErrorCode.REVISION_NOT_READY,
                    "Restored text requires fresh approved embeddings.",
                )
            rows.append(
                ProjectionRow(
                    **body.model_dump(),
                    vector=vectors[index] if vectors is not None else row.vector,
                )
            )
        return tuple(rows)

    def embedded(
        self,
        item: EmbeddedDocument,
        profile: EmbeddingProfile,
        checkpoint: Callable[[Checkpoint], Checkpoint],
        *,
        vectors: tuple[tuple[float, ...], ...] | None = None,
    ) -> EmbeddedDocument:
        """The caller supplies a currently approved profile and resealed checkpoints."""
        self.mapping.source(item)
        if (profile.org_id, profile.kg_id) != (
            self.mapping.target.org_id,
            self.mapping.target.kg_id,
        ):
            raise inaccessible_reference()
        try:
            item = EmbeddedDocument.model_validate(item)
            rows = self._projection_rows(item.rows, item.profile, profile, vectors)
            chunk, resolved = (
                checkpoint(item.chunk_checkpoint),
                checkpoint(item.resolved_checkpoint),
            )
            for old, new in ((item.chunk_checkpoint, chunk), (item.resolved_checkpoint, resolved)):
                if (new.org_id, new.kg_id, new.job_id, new.stage, new.artifact_id) != (
                    self.mapping.target.org_id,
                    self.mapping.target.kg_id,
                    old.job_id,
                    old.stage,
                    old.artifact_id,
                ):
                    raise self.inconsistent()
            return EmbeddedDocument.model_validate(
                item.model_dump()
                | self.mapping.source(item).model_dump()
                | {
                    "chunk_checkpoint": chunk,
                    "resolved_checkpoint": resolved,
                    "profile": profile,
                    "profile_selection_revision": 1,
                    "import_origin": self.import_origin(item.import_origin)
                    if item.import_origin
                    else None,
                    "rows": tuple(rows),
                }
            )
        except ValidationError:
            raise self.inconsistent() from None

    def curation(
        self,
        item: CurationEmbeddedDocument,
        profile: EmbeddingProfile,
        *,
        vectors: tuple[tuple[float, ...], ...] | None = None,
    ) -> CurationEmbeddedDocument:
        if (item.org_id, item.kg_id) != (self.mapping.origin.org_id, self.mapping.origin.kg_id) or (
            (profile.org_id, profile.kg_id)
            != (self.mapping.target.org_id, self.mapping.target.kg_id)
        ):
            raise inaccessible_reference()
        try:
            item = CurationEmbeddedDocument.model_validate(item)
            directive = item.directive
            restored: ManualNode | ManualIdentity | ManualRelation
            if isinstance(directive, ManualNode):
                restored = self.mapping.manual_node(directive)
            elif isinstance(directive, ManualIdentity):
                restored = self.mapping.manual_identity(directive)
            else:
                restored = self.mapping.manual_relation(directive)
            rows = self._projection_rows(item.rows, item.profile, profile, vectors)
            return CurationEmbeddedDocument.model_validate(
                item.model_dump()
                | self.mapping.target.model_dump()
                | {
                    "curation": self.mapping.curation_ref(item.curation),
                    "directive": restored,
                    "profile": profile,
                    "profile_selection_revision": 1,
                    "rows": tuple(rows),
                }
            )
        except ValidationError:
            raise self.inconsistent() from None
