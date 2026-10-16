"""Small, unambiguous URL-encoded forms; file bytes use the existing upload API."""

import re
from urllib.parse import parse_qsl

from starlette.requests import Request

from domain_kg.errors import DomainError, ErrorCode

FORM_LIMIT = 64 * 1024


def decode_form(body: bytes, content_type: str) -> dict[str, str]:
    if len(body) > FORM_LIMIT:
        raise DomainError(ErrorCode.QUOTA_EXCEEDED, "The form exceeds its byte budget.")
    if content_type.split(";", 1)[0].strip().lower() != "application/x-www-form-urlencoded":
        raise DomainError(ErrorCode.VALIDATION_ERROR, "The form encoding is not supported.")
    try:
        text = body.decode("utf-8")
        if re.search(r"%(?![0-9a-fA-F]{2})", text):
            raise ValueError("invalid percent escape")
        pairs = parse_qsl(
            text,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=100,
            encoding="utf-8",
            errors="strict",
        )
        fields: dict[str, str] = {}
        for key, value in pairs:
            if not key or key in fields or "\x00" in key or "\x00" in value:
                raise ValueError("ambiguous form fields")
            fields[key] = value
        return fields
    except (UnicodeError, ValueError):
        raise DomainError(ErrorCode.VALIDATION_ERROR, "The form fields are invalid.") from None


async def read_form(request: "Request") -> dict[str, str]:
    """Stop at the form byte ceiling instead of buffering an unbounded request."""
    content_type = request.headers.get("content-type", "")
    decode_form(b"", content_type)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > FORM_LIMIT:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "The form exceeds its byte budget.")
        body.extend(chunk)
    return decode_form(bytes(body), content_type)
