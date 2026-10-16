"""Reject oversized browser forms while streaming, before reading the whole body."""

import pytest
from starlette.requests import Request

from domain_kg.console.forms import FORM_LIMIT, read_form
from domain_kg.errors import DomainError, ErrorCode


async def test_form_stops_reading_at_byte_budget():
    received = 0
    chunks = iter((b"x" * FORM_LIMIT, b"x", b"must-not-be-read"))

    async def receive():
        nonlocal received
        received += 1
        return {"type": "http.request", "body": next(chunks), "more_body": True}

    request = Request(
        {"type": "http", "headers": [(b"content-type", b"application/x-www-form-urlencoded")]},
        receive,
    )
    with pytest.raises(DomainError) as error:
        await read_form(request)
    assert error.value.code == ErrorCode.QUOTA_EXCEEDED and received == 2


async def test_invalid_encoding_does_not_consume_request_body():
    async def receive():
        pytest.fail("unsupported form encodings must fail before reading the body")

    request = Request(
        {"type": "http", "headers": [(b"content-type", b"application/json")]}, receive
    )
    with pytest.raises(DomainError) as error:
        await read_form(request)
    assert error.value.code == ErrorCode.VALIDATION_ERROR
