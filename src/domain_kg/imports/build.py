"""Prepare honest legacy claims and byte-faithful context from an owned export."""

import hashlib
import json
from typing import TypedDict
from uuid import UUID, uuid5

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.models import AssertionRecord, DomainSchema, SupportRecord
from domain_kg.imports.legacy import MAX_BYTES
from domain_kg.imports.legacy_models import LegacyGraph
from domain_kg.imports.prepared import PreparedImport
from domain_kg.imports.provenance import ImportOrigin
from domain_kg.ingestion.chunking import chunk_document
from domain_kg.ingestion.models import ChunkConfig, ParsedBlock, ParsedDocument, SourceLocation
from domain_kg.ingestion.resolved import ResolvedAssertion, ResolvedEntity
from domain_kg.jobs.models import Checkpoint
from domain_kg.materials.models import BlobRef
from domain_kg.models import ScopedRef, SourceVersion
from domain_kg.revisions.projections import ProjectionBody

PARSER_VERSION = "legacy-export-text-v1"


class _NamespaceArgs(TypedDict):
    org_id: UUID
    kg_id: UUID


def snapshot_document(blob: BlobRef, body: bytes) -> ParsedDocument:
    blob = BlobRef.model_validate(blob)
    if len(body) > MAX_BYTES:
        raise DomainError(ErrorCode.QUOTA_EXCEEDED, "The export exceeds its byte budget.")
    if len(body) != blob.size_bytes or hashlib.sha256(body).hexdigest() != blob.sha256:
        raise DomainError(ErrorCode.EVIDENCE_VALIDATION_FAILED, "The export checksum differs.")
    try:
        text = body.decode("utf-8")
    except UnicodeError:
        raise DomainError(ErrorCode.UNSUPPORTED_FORMAT, "The export must be UTF-8 text.") from None
    source = SourceVersion(**{field: getattr(blob, field) for field in SourceVersion.model_fields})
    identifier = uuid5(blob.version_id, "legacy/raw-export-block")
    block = ParsedBlock(
        **source.model_dump(),
        id=identifier,
        text=text,
        location=SourceLocation(kind="text", block_id=identifier),
    )
    document = ParsedDocument(blob=blob, blocks=(block,), parser_version=PARSER_VERSION)
    chunks = chunk_document(document, ChunkConfig())
    return ParsedDocument.model_validate(document.model_copy(update={"chunks": tuple(chunks)}))


def owned_id(origin: ImportOrigin, kind: str, legacy_source: str, identifier: str) -> UUID:
    return legacy_owned_id(origin, origin.origin, kind, legacy_source, identifier)


def legacy_owned_id(
    source: SourceVersion, origin: str, kind: str, legacy_source: str, identifier: str
) -> UUID:
    return uuid5(
        source.org_id,
        json.dumps(
            [
                str(source.kg_id),
                str(source.material_id),
                str(source.version_id),
                "legacy",
                origin,
                kind,
                legacy_source,
                identifier,
            ],
            separators=(",", ":"),
        ),
    )


def build_import(
    graph: LegacyGraph,
    origin: ImportOrigin,
    parsed: ParsedDocument,
    schema: DomainSchema,
    chunk_checkpoint: Checkpoint,
) -> PreparedImport:
    graph, origin = LegacyGraph.model_validate(graph), ImportOrigin.model_validate(origin)
    parsed = ParsedDocument.model_validate(parsed)
    schema = DomainSchema.model_validate(schema)
    source = SourceVersion(
        **{field: getattr(parsed.blob, field) for field in SourceVersion.model_fields}
    )
    if (
        not origin.matches(source)
        or graph.namespace.org_id != source.org_id
        or graph.namespace.kg_id != source.kg_id
        or graph.origin != origin.origin
        or graph.raw_sha256 != parsed.blob.sha256
        or origin.graph_hash != hashlib.sha256(graph.model_dump_json().encode()).hexdigest()
        or parsed.parser_version != PARSER_VERSION
        or len(parsed.blocks) != 1
        or hashlib.sha256(parsed.blocks[0].text.encode()).hexdigest() != parsed.blob.sha256
        or not parsed.chunks
    ):
        raise inaccessible_reference()
    ns: _NamespaceArgs = {"org_id": source.org_id, "kg_id": source.kg_id}
    rows = [
        ProjectionBody(**ns, id=chunk.id, kind="chunk", source=source, chunk=chunk)
        for chunk in parsed.chunks
    ]
    observations = {}
    unlocated = False

    def anchor(*values: str) -> UUID:
        nonlocal unlocated
        for chunk in parsed.chunks:
            if all(value in chunk.text for value in values):
                return chunk.id
        unlocated = True
        return parsed.chunks[0].id  # File context only: no assertion/observation span is claimed.

    for entity in graph.entities:
        observation = ResolvedEntity(
            **source.model_dump(),
            id=owned_id(origin, "observation", entity.source_id, entity.legacy_id),
            chunk_id=anchor(entity.name),
            entity=ScopedRef(**ns, id=entity.entity_id),
            name=entity.name,
            entity_type=entity.entity_type,
            description="",
            spans=(),
        )
        key = (entity.source_id, entity.entity_id)
        if key in observations:
            raise DomainError(
                ErrorCode.EVIDENCE_VALIDATION_FAILED, "Legacy entity ownership is ambiguous."
            )
        observations[key] = observation
        rows.append(
            ProjectionBody(
                **ns, id=observation.id, kind="observation", source=source, observation=observation
            )
        )
    for relation in graph.relations:
        subject = observations.get((relation.source_id, relation.subject_id))
        target = observations.get((relation.source_id, relation.target_id))
        if subject is None or target is None:
            raise inaccessible_reference()
        fact = AssertionRecord(
            **ns,
            id=relation.assertion_id,
            subject=subject.entity,
            target=target.entity,
            predicate=relation.predicate,
            polarity=relation.polarity,
        )
        identifier = owned_id(origin, "support", relation.source_id, relation.legacy_id)
        support = SupportRecord(
            **ns,
            id=identifier,
            assertion=ScopedRef(**ns, id=fact.id),
            version=ScopedRef(**ns, id=source.version_id),
            status="imported_unverified",
            spans=(),
        )
        resolved = ResolvedAssertion(
            assertion=fact,
            support=support,
            subject_observation_id=subject.id,
            target_observation_id=target.id,
        )
        rows.append(
            ProjectionBody(
                **ns,
                id=identifier,
                kind="support",
                source=source,
                assertion=resolved,
                subject_observation=subject,
                target_observation=target,
            )
        )
    return PreparedImport(
        **source.model_dump(),
        import_origin=origin,
        domain_schema=schema,
        chunk_checkpoint=chunk_checkpoint,
        rows=tuple(rows),
        warnings=graph.warnings
        + ("LEGACY_CLAIMS_UNVERIFIED",)
        + (("LEGACY_RECORD_LOCATION_UNVERIFIED",) if unlocated else ()),
    )
