"""Strict source/schema validation around upstream LightRAG extraction.

Model output is data. Only this server constructs engine names and canonical
commands, and no native LightRAG storage or mixed-source summary is served here.
"""

import json
import re
from importlib.metadata import version
from typing import Any, Protocol
from uuid import UUID

from pydantic import ValidationError

from domain_kg.engine.source_mapping import engine_name, engine_source
from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.models import DomainSchema
from domain_kg.ingestion.models import ExtractionBatch, ExtractionContent, ParsedChunk, Span

NEGATION = re.compile(
    r"\b(?:not|never|no|without|neither|cannot|can't|doesn't|fails? to|lacks?)\b"
    r"|不(?:会|能|是|使用|导致)|未(?:能|发现|证明)|并非",
    re.I,
)
IDENTIFIER = re.compile(
    r"(?:urn:[A-Za-z0-9][A-Za-z0-9:._-]{1,250}|doi:10\.\d{4,9}/\S{1,300}|HGNC:\d{1,12}|https://[^\s]{1,400})\Z"
)


def invalid() -> DomainError:
    return DomainError(
        ErrorCode.EVIDENCE_VALIDATION_FAILED,
        "Extraction fields must match the authorized source and domain schema.",
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
                raise invalid()
        return "\n".join(span.quote for span in spans)

    entities = {entity.local_id: entity for entity in batch.entities}
    if len(entities) != len(batch.entities):
        raise invalid()
    for entity in batch.entities:
        evidence = check(entity.spans)
        if (
            entity.entity_type not in schema.entity_types
            or entity.name not in evidence
            or (entity.description and entity.description not in evidence)
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
            raise invalid()
    predicates = {predicate.name: predicate for predicate in schema.predicates}
    for assertion in batch.assertions:
        evidence = check(assertion.spans)
        subject, target = entities.get(assertion.subject), entities.get(assertion.target)
        predicate = predicates.get(assertion.predicate)
        if (
            subject is None
            or target is None
            or predicate is None
            or subject.name not in evidence
            or target.name not in evidence
            or (
                subject.entity_type not in predicate.subject_types
                or target.entity_type not in predicate.target_types
            )
            or (assertion.polarity == "positive" and NEGATION.search(evidence))
        ):
            raise invalid()
        for qualifier in assertion.qualifiers:
            if qualifier.value not in check(qualifier.spans):
                raise invalid()
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
            content = ExtractionContent.model_validate(await self.provider.complete(chunk, schema))
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
                            "type": entity.entity_type,
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
                "entity_extract_max_records": 150,
                "entity_extract_max_entities": 50,
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
