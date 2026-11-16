"""Conservative UTF-8 token upper bounds, retaining exact canonical source offsets.

One UTF-8 byte per input token is deliberately conservative for byte-backed text
tokenizers. A 256-token extraction margin covers chat framing; responses reserve
their entire configured maximum. This is not a model quality guarantee.
"""

import json
from uuid import uuid5

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.models import DomainSchema
from domain_kg.ingestion.chunking import overlap_tail, split_span, tokens
from domain_kg.ingestion.models import ChunkConfig, ParsedChunk, Span
from domain_kg.jobs.diagnostics import EVIDENCE_DETAILS
from domain_kg.providers.extraction import evidence_correction, extraction_payload
from domain_kg.providers.profiles import ProviderProfile

WINDOW_VERSION = "model-window-v6-unicode"
CORRECTION_RESERVE = evidence_correction(max(EVIDENCE_DETAILS, key=lambda text: len(text.encode())))


def adaptive_chunk_config(profile: ProviderProfile) -> ChunkConfig:
    """Choose a planning ceiling; fit_source_chunks still measures each complete request."""
    available = min(
        profile.extraction_context_tokens - profile.max_completion_tokens - 256,
        profile.embedding_context_tokens - 128,
    )
    if available < 1:
        raise DomainError(
            ErrorCode.QUOTA_EXCEEDED,
            "Model context cannot hold the schema and one source character.",
        )
    defaults = ChunkConfig()
    return ChunkConfig(
        target_tokens=available,
        overlap_tokens=min(
            defaults.overlap_tokens,
            available * defaults.overlap_tokens // defaults.target_tokens,
        ),
    )


def extraction_fits(chunk: ParsedChunk, schema: DomainSchema, profile: ProviderProfile) -> bool:
    # Preparation must leave room for the one corrective request as well as the
    # initial request. The actual correction remains checked before transmission.
    return extraction_request_fits(
        extraction_payload(chunk, schema, profile, correction=CORRECTION_RESERVE), profile
    )


def extraction_request_fits(payload: dict[str, object], profile: ProviderProfile) -> bool:
    """Count the actual prompt, including correction instructions, before transmission."""
    prompt = {"messages": payload["messages"]}
    if profile.schema_in_context:
        prompt["response_format"] = payload["response_format"]
    size = len(json.dumps(prompt, ensure_ascii=False, separators=(",", ":")).encode())
    return size + profile.max_completion_tokens + 256 <= profile.extraction_context_tokens


def split_embedding_input(text: str, *, context_tokens: int, prefix: str = "") -> list[str]:
    available = context_tokens - 128 - len(prefix.encode())
    if available < 4:
        raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Embedding context cannot hold source text.")
    parts, start = [], 0
    while start < len(text):
        low, high, end = start + 1, min(len(text), start + available), start
        while low <= high:
            middle = (low + high) // 2
            if len(text[start:middle].encode()) <= available:
                end, low = middle, middle + 1
            else:
                high = middle - 1
        if end == start:
            raise DomainError(
                ErrorCode.QUOTA_EXCEEDED, "Embedding context cannot hold source text."
            )
        parts.append(text[start:end])
        start = end
    return parts


def source_window(chunk: ParsedChunk, original: Span, start: int, end: int) -> ParsedChunk:
    span = Span(
        location=original.location,
        start=original.start + start,
        end=original.start + end,
        quote=original.quote[start:end],
    )
    identifier = uuid5(chunk.id, WINDOW_VERSION + span.model_dump_json())
    return ParsedChunk.model_validate(
        chunk.model_copy(
            update={
                "id": identifier,
                "text": span.quote,
                "spans": (span,),
                "chunker_version": chunk.chunker_version + "/" + WINDOW_VERSION,
            }
        )
    )


def fit_source_chunks(
    chunks: list[ParsedChunk], schema: DomainSchema, profile: ProviderProfile
) -> list[ParsedChunk]:
    result = []

    def fits(chunk: ParsedChunk) -> bool:
        return extraction_fits(chunk, schema, profile) and (
            len(chunk.text.encode()) + 128 <= profile.embedding_context_tokens
        )

    for chunk in chunks:
        if fits(chunk):
            result.append(chunk)
            continue
        pieces: list[Span] = []
        for original in chunk.spans:
            if fits(source_window(chunk, original, 0, len(original.quote))):
                pieces.append(original)
                continue
            capacity = max(1, tokens(original.quote) // 2)
            while True:
                divided = split_span(original, capacity, overlap=min(100, capacity // 5))
                if all(
                    fits(
                        source_window(
                            chunk, original, s.start - original.start, s.end - original.start
                        )
                    )
                    for s in divided
                ):
                    pieces.extend(divided)
                    break
                if capacity == 1:
                    raise DomainError(
                        ErrorCode.QUOTA_EXCEEDED,
                        "Model context cannot hold the schema and one source character.",
                    )
                capacity = max(1, capacity // 2)
            if len(pieces) > 100000:
                raise DomainError(
                    ErrorCode.QUOTA_EXCEEDED, "Source exceeds the model-window budget."
                )

        def packed(spans: list[Span], parent: ParsedChunk = chunk) -> ParsedChunk:
            return ParsedChunk.model_validate(
                parent.model_copy(
                    update={
                        "id": uuid5(
                            parent.id, WINDOW_VERSION + "".join(s.model_dump_json() for s in spans)
                        ),
                        "text": "\n".join(s.quote for s in spans),
                        "spans": tuple(spans),
                        "chunker_version": parent.chunker_version + "/" + WINDOW_VERSION,
                    }
                )
            )

        pending: list[Span] = []
        produced = 0
        for piece in pieces:
            candidate = [*pending, piece]
            shared = bool(
                pending and pending[-1].location == piece.location and piece.start < pending[-1].end
            )
            if pending and (shared or not fits(packed(candidate))):
                result.append(packed(pending))
                produced += 1

                def tail_fits(tail: list[Span], current: Span = piece) -> bool:
                    return fits(packed([*tail, current]))

                pending = (
                    []
                    if shared
                    else overlap_tail(
                        pending,
                        100,
                        fits=tail_fits,
                    )
                )
            pending.append(piece)
            if produced >= 128 or len(result) > 100000:
                raise DomainError(
                    ErrorCode.QUOTA_EXCEEDED, "Source exceeds the model-window budget."
                )
        if pending:
            result.append(packed(pending))
    return result
