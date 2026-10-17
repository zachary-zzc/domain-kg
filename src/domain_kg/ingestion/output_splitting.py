"""Adapt to a model's output ceiling without accepting or repairing invalid facts."""

from collections.abc import Awaitable, Callable
from uuid import UUID, uuid5

from domain_kg.graph.models import DomainSchema
from domain_kg.ingestion.extraction import validate_extraction
from domain_kg.ingestion.models import (
    ExtractedAssertion,
    ExtractedEntity,
    ExtractionBatch,
    ParsedChunk,
    Span,
)
from domain_kg.providers.budget import source_window
from domain_kg.providers.extraction import ExtractionOutputLimit


def _halves(chunk: ParsedChunk) -> tuple[ParsedChunk, ParsedChunk]:
    if len(chunk.spans) > 1:
        middle = len(chunk.spans) // 2
        groups = (chunk.spans[:middle], chunk.spans[middle:])

        def window(spans: tuple[Span, ...]) -> ParsedChunk:
            return ParsedChunk.model_validate(
                chunk.model_copy(
                    update={
                        "id": uuid5(
                            chunk.id,
                            "output-window-v1/" + "".join(s.model_dump_json() for s in spans),
                        ),
                        "spans": spans,
                        "text": "\n".join(s.quote for s in spans),
                        "chunker_version": chunk.chunker_version + "/output-window-v1",
                    }
                )
            )

        return window(groups[0]), window(groups[1])
    if len(chunk.spans) != 1 or len(chunk.spans[0].quote) < 2:
        raise ExtractionOutputLimit()
    span = chunk.spans[0]
    middle = len(span.quote) // 2
    # Prefer a nearby word/line boundary but never drop whitespace or characters.
    candidates = [
        i + 1 for i, c in enumerate(span.quote) if c.isspace() and 0 < i + 1 < len(span.quote)
    ]
    if candidates:
        middle = min(candidates, key=lambda i: abs(i - middle))
    return source_window(chunk, span, 0, middle), source_window(
        chunk, span, middle, len(span.quote)
    )


async def extract_with_splitting(
    chunk: ParsedChunk,
    schema: DomainSchema,
    profile_id: UUID,
    produce: Callable[[ParsedChunk], Awaitable[ExtractionBatch]],
    *,
    max_depth: int = 2,
) -> ExtractionBatch:
    if not 0 <= max_depth <= 2:
        raise ValueError("At most two output-splitting levels are permitted.")
    try:
        return validate_extraction(chunk, schema, await produce(chunk))
    except ExtractionOutputLimit:
        if max_depth == 0:
            raise
    children = _halves(chunk)
    entities: list[ExtractedEntity] = []
    assertions: list[ExtractedAssertion] = []
    warnings: list[str] = []
    for position, child in enumerate(children):
        result = await extract_with_splitting(
            child, schema, profile_id, produce, max_depth=max_depth - 1
        )
        identifiers = {e.local_id: f"{position}/{e.local_id}" for e in result.entities}
        entities.extend(
            e.model_copy(update={"local_id": identifiers[e.local_id]}) for e in result.entities
        )
        assertions.extend(
            a.model_copy(
                update={"subject": identifiers[a.subject], "target": identifiers[a.target]}
            )
            for a in result.assertions
        )
        warnings.extend(result.warnings)
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
            entities=tuple(entities),
            assertions=tuple(assertions),
            warnings=(*warnings, "model_output_split:2"),
        ),
    )
