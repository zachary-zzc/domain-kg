"""Measured token bounds with exact source offsets and intact small table rows."""

import hashlib
import json
from collections.abc import Callable, Sequence
from functools import lru_cache
from uuid import uuid5

import tiktoken

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.ingestion.models import ChunkConfig, ParsedBlock, ParsedChunk, ParsedDocument, Span

VERSION = "source-chunker-cl100k-v3"


@lru_cache(maxsize=1)
def encoding() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def tokens(text: str) -> int:
    # Special-token-like material is ordinary untrusted text, never an instruction.
    return len(encoding().encode_ordinary(text))


@lru_cache(maxsize=1)
def _max_token_bytes() -> int:
    return max(len(value) for value in encoding().token_byte_values())


def fits(text: str, limit: int) -> bool:
    # Character count is a lower bound on UTF-8 bytes. Reject obviously large
    # blocks without allocating token IDs for a whole multi-megabyte paragraph.
    return len(text) <= limit * _max_token_bytes() and tokens(text) <= limit


def overlap_tail(
    spans: Sequence[Span],
    limit: int,
    *,
    fits: Callable[[list[Span]], bool] | None = None,
    split_rows: bool = False,
) -> list[Span]:
    """Keep a bounded suffix; split rows only when their source already requires subdivision."""
    if limit <= 0:
        return []
    overlap: list[Span] = []

    def acceptable(candidate: list[Span]) -> bool:
        return tokens("\n".join(s.quote for s in candidate)) <= limit and (
            fits is None or fits(candidate)
        )

    for previous in reversed(spans):
        candidate = [previous, *overlap]
        if acceptable(candidate):
            overlap = candidate
            continue
        if previous.location.row is not None and not split_rows:
            break
        low, high = 1, len(previous.quote) - 1
        best = overlap
        while low <= high:
            middle = (low + high) // 2
            tail = Span(
                location=previous.location,
                start=previous.start + middle,
                end=previous.end,
                quote=previous.quote[middle:],
            )
            candidate = [tail, *overlap]
            if acceptable(candidate):
                best, high = candidate, middle - 1
            else:
                low = middle + 1
        overlap = best
        break
    return overlap


def _split(block: ParsedBlock, config: ChunkConfig) -> list[Span]:
    spans, start = [], 0
    while start < len(block.text):
        low, high = start + 1, min(len(block.text), start + max(4096, config.target_tokens * 16))
        end = start
        while low <= high:
            middle = (low + high) // 2
            if tokens(block.text[start:middle]) <= config.target_tokens:
                end, low = middle, middle + 1
            else:
                high = middle - 1
        if end == start:
            raise DomainError(
                ErrorCode.VALIDATION_ERROR, "Token limit cannot hold one source character."
            )
        spans.append(
            Span(location=block.location, start=start, end=end, quote=block.text[start:end])
        )
        if end == len(block.text):
            break
        next_start = end
        if config.overlap_tokens and tokens(block.text[start:end]) > config.overlap_tokens:
            overlap = overlap_tail([spans[-1]], config.overlap_tokens, split_rows=True)
            next_start = overlap[0].start if overlap else end
        start = max(start + 1, next_start)
    return spans


def chunk_document(document: ParsedDocument, config: ChunkConfig) -> list[ParsedChunk]:
    document, config = ParsedDocument.model_validate(document), ChunkConfig.model_validate(config)
    if document.chunks and not document.blocks:
        raise DomainError(ErrorCode.VALIDATION_ERROR, "Structured source blocks are required.")
    result: list[ParsedChunk] = []
    pending: list[Span] = []
    group: tuple[object, ...] | None = None
    config_hash = hashlib.sha256(config.model_dump_json().encode()).hexdigest()

    def emit(spans: list[Span]) -> None:
        if not spans:
            return
        text = "\n".join(span.quote for span in spans)
        if tokens(text) > config.target_tokens:
            raise AssertionError("chunk token invariant")
        identity = json.dumps(
            [(str(span.location.block_id), span.start, span.end) for span in spans]
        )
        result.append(
            ParsedChunk(
                org_id=document.blob.org_id,
                kg_id=document.blob.kg_id,
                material_id=document.blob.material_id,
                version_id=document.blob.version_id,
                id=uuid5(
                    document.blob.version_id,
                    f"{VERSION}/{document.parser_version}/{config_hash}/{identity}",
                ),
                text=text,
                spans=tuple(spans),
                parser_version=document.parser_version,
                chunker_version=VERSION,
            )
        )
        if len(result) > 100000:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Source produces too many chunks.")

    for block in document.blocks:
        if not block.text:
            continue
        key = (
            block.location.kind,
            block.location.heading,
            block.location.table,
            block.location.text_origin,
            block.location.image_id,
            block.location.sheet,
            block.location.slide,
        )
        if group != key:
            emit(pending)
            pending, group = [], key
        spans = (
            [Span(location=block.location, start=0, end=len(block.text), quote=block.text)]
            if fits(block.text, config.target_tokens)
            else _split(block, config)
        )
        for span in spans:
            shared = bool(
                pending and pending[-1].location == span.location and span.start < pending[-1].end
            )
            if pending and (
                shared
                or tokens("\n".join(item.quote for item in [*pending, span])) > config.target_tokens
            ):
                emit(pending)

                # Long-block pieces already share source characters. Do not add
                # those characters a second time inside the next chunk.
                def tail_fits(tail: list[Span], current: Span = span) -> bool:
                    return (
                        tokens("\n".join(s.quote for s in [*tail, current])) <= config.target_tokens
                    )

                pending = (
                    []
                    if shared
                    else overlap_tail(
                        pending,
                        config.overlap_tokens,
                        fits=tail_fits,
                    )
                )
            pending.append(span)
    emit(pending)
    return result
