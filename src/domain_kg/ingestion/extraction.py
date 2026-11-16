"""Strict source/schema validation around upstream LightRAG extraction.

Model output is data. Only this server constructs engine names and canonical
commands, and no native LightRAG storage or mixed-source summary is served here.
"""

import json
import re
from collections import Counter
from importlib.metadata import version
from typing import Any, Protocol
from uuid import UUID

from pydantic import ValidationError

from domain_kg.engine.source_mapping import engine_name, engine_source
from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.models import DomainSchema
from domain_kg.ingestion.models import (
    ExtractedAssertion,
    ExtractedEntity,
    ExtractionBatch,
    ExtractionContent,
    ExtractionResult,
    ParsedChunk,
    Span,
)

IDENTIFIER = re.compile(
    r"(?:urn:[A-Za-z0-9][A-Za-z0-9:._-]{1,250}|doi:10\.\d{4,9}/\S{1,300}|HGNC:\d{1,12}|https://[^\s]{1,400})\Z"
)


def invalid(
    message: str = "Extraction fields must match the authorized source and domain schema.",
) -> DomainError:
    return DomainError(
        ErrorCode.EVIDENCE_VALIDATION_FAILED,
        message,
    )


def merge_validated_extractions(
    original: ExtractionResult, corrected: ExtractionResult
) -> ExtractionResult:
    """Union validated facts without treating temporary IDs as shared identities."""
    entities: dict[str, ExtractedEntity] = {}
    assertions: dict[str, ExtractedAssertion] = {}
    used_ids: set[str] = set()
    for content in (corrected, original):
        endpoints = {key for a in content.assertions for key in (a.subject, a.target)}
        local: dict[str, str] = {}
        for entity in content.entities:
            if content is original and entity.local_id not in endpoints:
                continue
            identity = entity.model_dump_json(exclude={"local_id"})
            if identity not in entities:
                local_id = entity.local_id
                suffix = len(entities)
                while local_id in used_ids:
                    local_id = f"e{suffix}"
                    suffix += 1
                used_ids.add(local_id)
                entities[identity] = entity.model_copy(update={"local_id": local_id})
            local[entity.local_id] = entities[identity].local_id
        for assertion in content.assertions:
            remapped = assertion.model_copy(
                update={"subject": local[assertion.subject], "target": local[assertion.target]}
            )
            # A second confidence estimate is not independent evidence ownership.
            identity = remapped.model_dump_json(exclude={"confidence"})
            assertions.setdefault(identity, remapped)
    return ExtractionResult(
        entities=tuple(entities.values()),
        assertions=tuple(assertions.values()),
        warnings=corrected.warnings,
    )


def validated_subset(
    chunk: ParsedChunk,
    schema: DomainSchema,
    content: ExtractionContent,
    profile_id: UUID,
    *,
    rejected_entities: int = 0,
    rejected_assertions: int = 0,
) -> ExtractionResult:
    """Keep only independently validated facts; never amend their evidence or qualifiers."""

    def check(
        entities: tuple[ExtractedEntity, ...], assertions: tuple[ExtractedAssertion, ...]
    ) -> ExtractionBatch:
        return validate_extraction(
            chunk,
            schema,
            ExtractionBatch(
                org_id=chunk.org_id,
                kg_id=chunk.kg_id,
                material_id=chunk.material_id,
                version_id=chunk.version_id,
                chunk_id=chunk.id,
                schema_id=schema.id,
                schema_revision=schema.revision,
                provider_profile_id=profile_id,
                entities=entities,
                assertions=assertions,
            ),
        )

    check((), ())  # A source/schema mismatch is never a discardable claim.
    identifiers = Counter(entity.local_id for entity in content.entities)
    accepted_entities = []
    for entity in content.entities:
        try:
            if identifiers[entity.local_id] != 1:
                raise invalid("The model returned duplicate entity identifiers.")
            check((entity,), ())
        except DomainError as error:
            if error.code != ErrorCode.EVIDENCE_VALIDATION_FAILED:
                raise
            rejected_entities += 1
        else:
            accepted_entities.append(entity)
    entities = tuple(accepted_entities)
    accepted_assertions = []
    for assertion in content.assertions:
        try:
            check(entities, (assertion,))
        except ValidationError:
            # Typed claims can still reference an entity that was rejected above.
            rejected_assertions += 1
        except DomainError as error:
            if error.code != ErrorCode.EVIDENCE_VALIDATION_FAILED:
                raise
            rejected_assertions += 1
        else:
            accepted_assertions.append(assertion)
    result = check(entities, tuple(accepted_assertions))
    warnings = tuple(
        f"extraction_rejected_{kind}:{count}"
        for kind, count in (("entities", rejected_entities), ("assertions", rejected_assertions))
        if count
    )
    return ExtractionResult(
        entities=result.entities, assertions=result.assertions, warnings=warnings
    )


def validate_extraction(
    chunk: ParsedChunk, schema: DomainSchema, batch: ExtractionBatch
) -> ExtractionBatch:
    try:
        chunk, schema, batch = (
            ParsedChunk.model_validate(chunk),
            DomainSchema.model_validate(schema),
            ExtractionBatch.model_validate(batch),
        )
    except ValidationError as exc:
        raise invalid() from exc
    if (
        batch.org_id,
        batch.kg_id,
        batch.material_id,
        batch.version_id,
        batch.chunk_id,
        batch.schema_id,
        batch.schema_revision,
    ) != (
        chunk.org_id,
        chunk.kg_id,
        chunk.material_id,
        chunk.version_id,
        chunk.id,
        schema.id,
        schema.revision,
    ) or (schema.org_id, schema.kg_id) != (chunk.org_id, chunk.kg_id):
        raise invalid()

    def check(spans: tuple[Span, ...]) -> str:
        for span in spans:
            if not any(
                span.location == known.location
                and span.start >= known.start
                and span.end <= known.end
                and span.quote == known.quote[span.start - known.start : span.end - known.start]
                for known in chunk.spans
            ):
                raise invalid(
                    "An evidence quotation or location does not match the original source."
                )
        return "\n".join(span.quote for span in spans)

    entities = {entity.local_id: entity for entity in batch.entities}
    if len(entities) != len(batch.entities):
        raise invalid("The model returned duplicate entity identifiers.")
    for entity in batch.entities:
        evidence = check(entity.spans)
        if not schema.allows_entity_type(entity.entity_type):
            raise invalid("An extracted entity uses an unsupported type.")
        if entity.name not in evidence:
            raise invalid("An extracted entity name is absent from its cited source passages.")
        if (
            (entity.description and entity.description not in evidence)
            or any(
                not alias or len(alias) > 1000 or alias not in evidence for alias in entity.aliases
            )
            or (
                entity.identifier is not None
                and (
                    entity.identifier not in evidence or not IDENTIFIER.fullmatch(entity.identifier)
                )
            )
        ):
            raise invalid(
                "An entity description, alias or identifier is not grounded in its evidence."
            )
    for assertion in batch.assertions:
        evidence = check(assertion.spans)
        subject, target = entities.get(assertion.subject), entities.get(assertion.target)
        if subject is None or target is None:
            raise invalid("An extracted relation refers to an unknown entity.")
        if not schema.allows_relation(assertion.predicate, subject.entity_type, target.entity_type):
            raise invalid("An extracted relation uses an unsupported predicate or endpoint type.")
        if subject.name not in evidence or target.name not in evidence:
            raise invalid("The relation evidence does not contain both endpoint names.")
        # Polarity is a model judgment about this claim. A keyword elsewhere in
        # the cited text cannot establish its meaning or contradict that judgment.
        for qualifier in assertion.qualifiers:
            if qualifier.value not in check(qualifier.spans):
                raise invalid("An extracted qualifier is absent from its cited source passages.")
    return batch


class StructuredProvider(Protocol):
    id: UUID

    async def complete(self, chunk: ParsedChunk, schema: DomainSchema) -> dict[str, Any]: ...


class Extractor:
    def __init__(self, provider: StructuredProvider) -> None:
        self.provider = provider

    async def extract(self, chunk: ParsedChunk, schema: DomainSchema) -> ExtractionBatch:
        from lightrag.operate import extract_entities

        if version("lightrag-hku") != "1.5.7":
            raise DomainError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "The configured extraction engine version is unsupported.",
            )
        # Validate outside the upstream callback: LightRAG prefixes exceptions
        # by reconstructing their type with one argument, which cannot preserve
        # a DomainError(code, message) or its safe classification.
        try:
            content = ExtractionResult.model_validate(await self.provider.complete(chunk, schema))
            validated = validate_extraction(
                chunk,
                schema,
                ExtractionBatch(
                    org_id=chunk.org_id,
                    kg_id=chunk.kg_id,
                    material_id=chunk.material_id,
                    version_id=chunk.version_id,
                    chunk_id=chunk.id,
                    schema_id=schema.id,
                    schema_revision=schema.revision,
                    provider_profile_id=self.provider.id,
                    **content.model_dump(),
                ),
            )
        except ValidationError as exc:
            raise invalid() from exc
        # Retain source text independently for vector/text retrieval. A mention
        # belongs in the automatic graph only when a validated fact connects it.
        endpoints = {
            endpoint
            for assertion in validated.assertions
            for endpoint in (assertion.subject, assertion.target)
        }
        validated = validated.model_copy(
            update={"entities": tuple(e for e in validated.entities if e.local_id in endpoints)}
        )
        called = False
        native_names: dict[str, str] = {}

        async def completion(_prompt: str, **_kwargs: Any) -> str:
            nonlocal called
            called = True
            native_names.update(
                {
                    entity.local_id: engine_name(chunk, entity.local_id)
                    for entity in validated.entities
                }
            )
            return json.dumps(
                {
                    "entities": [
                        {
                            "name": native_names[entity.local_id],
                            # Upstream rejects punctuation in types (e.g. Test/Service).
                            # Keep its grammar opaque, like names; canonical source
                            # labels remain unchanged in the validated batch.
                            "type": engine_name(chunk, f"type/{entity.entity_type}"),
                            "description": "Source validated observation",
                        }
                        for entity in validated.entities
                    ],
                    "relationships": [
                        {
                            "source": native_names[assertion.subject],
                            "target": native_names[assertion.target],
                            "keywords": assertion.predicate,
                            "description": "Source validated relationship",
                        }
                        for assertion in validated.assertions
                    ],
                }
            )

        source = engine_source(chunk)
        results = await extract_entities(
            {
                source: {
                    "content": chunk.text,
                    "tokens": len(chunk.text),
                    "full_doc_id": str(chunk.version_id),
                    "chunk_order_index": 0,
                    "file_path": source,
                }
            },
            {
                "role_llm_funcs": {"extract": completion},
                "entity_extract_max_gleaning": 0,
                "entity_extract_max_records": max(
                    1, len(validated.entities) + len(validated.assertions)
                ),
                "entity_extract_max_entities": max(1, len(validated.entities)),
                "entity_extraction_use_json": True,
                "llm_model_max_async": 1,
                "addon_params": {"language": "English"},
            },
        )
        if not called:
            raise invalid()
        observed: set[str] = set()
        relationships: set[tuple[str, str]] = set()
        for nodes, edges in results:
            for values in nodes.values():
                for item in values:
                    if item["source_id"] != source:
                        raise invalid()
                    observed.add(str(item["entity_name"]).casefold())
            for values in edges.values():
                for item in values:
                    if item["source_id"] != source:
                        raise invalid()
                    relationships.add(
                        (str(item["src_id"]).casefold(), str(item["tgt_id"]).casefold())
                    )
        if observed != {value.casefold() for value in native_names.values()} or relationships != {
            (native_names[assertion.subject].casefold(), native_names[assertion.target].casefold())
            for assertion in validated.assertions
        }:
            raise invalid()
        return validated
