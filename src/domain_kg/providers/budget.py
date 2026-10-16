"""Conservative UTF-8 token upper bounds, retaining exact canonical source offsets.

One UTF-8 byte per input token is deliberately conservative for byte-backed text
tokenizers. A 256-token extraction margin covers chat framing; responses reserve
their entire configured maximum. This is not a model quality guarantee.
"""

import json
from uuid import uuid5

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.models import DomainSchema
from domain_kg.ingestion.chunking import tokens
from domain_kg.ingestion.models import ParsedChunk, Span
from domain_kg.providers.extraction import extraction_payload
from domain_kg.providers.profiles import ProviderProfile

WINDOW_VERSION = "model-window-v1"


def extraction_fits(chunk: ParsedChunk, schema: DomainSchema, profile: ProviderProfile) -> bool:
    payload = extraction_payload(chunk, schema, profile)
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
        produced = 0
        for original in chunk.spans:
            start = 0

            while start < len(original.quote):
                low, high, end = start + 1, len(original.quote), start
                while low <= high:
                    middle = (low + high) // 2
                    if fits(source_window(chunk, original, start, middle)):
                        end, low = middle, middle + 1
                    else:
                        high = middle - 1
                if end == start:
                    raise DomainError(
                        ErrorCode.QUOTA_EXCEEDED,
                        "Model context cannot hold the schema and one source character.",
                    )
                result.append(source_window(chunk, original, start, end))
                produced += 1
                if produced > 128 or len(result) > 100000:
                    raise DomainError(
                        ErrorCode.QUOTA_EXCEEDED, "Source exceeds the model-window budget."
                    )
                if end == len(original.quote):
                    break
                # Keep a small overlap, always making progress, capped at 100 cl100k tokens.
                overlap = min((end - start) // 5, 100)
                while overlap and tokens(original.quote[end - overlap : end]) > 100:
                    overlap -= 1
                start = max(start + 1, end - overlap)
    return result
