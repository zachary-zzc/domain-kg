"""Adapt to a model's output ceiling without accepting or repairing invalid facts."""

from collections.abc import Awaitable, Callable
from itertools import accumulate
from uuid import UUID, uuid5

from domain_kg.graph.models import DomainSchema
from domain_kg.ingestion.chunking import overlap_tail, split_span, tokens
from domain_kg.ingestion.extraction import validate_extraction
from domain_kg.ingestion.models import (
    ExtractedAssertion,
    ExtractedEntity,
    ExtractionBatch,
    ParsedChunk,
    Span,
)
from domain_kg.ingestion.warnings import merge_warnings
from domain_kg.providers.extraction import ExtractionOutputLimit

OUTPUT_WINDOW_VERSION = "output-window-v5-unicode"


def _overlap(spans: tuple[Span, ...], *, split_rows: bool = False) -> list[Span]:
    limit = min(100, tokens("\n".join(s.quote for s in spans)) // 5)
    return overlap_tail(spans, limit, split_rows=split_rows)


def _halves(chunk: ParsedChunk) -> tuple[ParsedChunk, ParsedChunk]:
    def window(spans: tuple[Span, ...]) -> ParsedChunk:
        return ParsedChunk.model_validate(
            chunk.model_copy(
                update={
                    "id": uuid5(
                        chunk.id,
                        OUTPUT_WINDOW_VERSION + "/" + "".join(s.model_dump_json() for s in spans),
                    ),
                    "spans": spans,
                    "text": "\n".join(s.quote for s in spans),
                    "chunker_version": chunk.chunker_version + "/" + OUTPUT_WINDOW_VERSION,
                }
            )
        )

    if not chunk.spans:
        raise ExtractionOutputLimit()
    weights = [tokens(span.quote) for span in chunk.spans]
    target = max(1, sum(weights) // 2)
    largest = max(range(len(weights)), key=weights.__getitem__)
    span = chunk.spans[largest]
    if weights[largest] <= target or len(span.quote) < 2:
        if len(chunk.spans) == 1:
            raise ExtractionOutputLimit()
        middle = min(
            enumerate(accumulate(weights[:-1]), 1), key=lambda pair: abs(pair[1] - target)
        )[0]
        left, right = chunk.spans[:middle], chunk.spans[middle:]
        overlap = _overlap(left)
        return window(left), window((*overlap, *right))

    # A PDF line or DOCX paragraph can contain most of the text. Splitting by
    # passage count would keep that oversized passage intact through every retry.
    within = max(1, target - sum(weights[:largest]))
    parts = split_span(span, max(within, max(tokens(c) for c in span.quote)))
    candidates = [s.end - span.start for s in parts[:-1]]
    if not candidates:
        raise ExtractionOutputLimit()
    middle = min(candidates, key=lambda i: abs(tokens(span.quote[:i]) - within))
    left_span = Span(
        location=span.location,
        start=span.start,
        end=span.start + middle,
        quote=span.quote[:middle],
    )
    left = (*chunk.spans[:largest], left_span)
    overlap = _overlap(left, split_rows=True)
    tail = next((s for s in overlap if s.location == span.location), None)
    start = tail.start - span.start if tail is not None else middle
    right_span = Span(
        location=span.location, start=span.start + start, end=span.end, quote=span.quote[start:]
    )
    preceding = tuple(s for s in overlap if s.location != span.location)
    return window(left), window((*preceding, right_span, *chunk.spans[largest + 1 :]))


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
            warnings=merge_warnings((*warnings, "model_output_split:2")),
        ),
    )
