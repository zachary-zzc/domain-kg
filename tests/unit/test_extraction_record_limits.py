"""Dense structured outputs use the existing bounded source splitting signal."""

import json

import httpx
import pytest
from test_extraction_validation import proposal, source
from test_provider_extraction import configured

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.providers.extraction import ExtractionClient, ExtractionOutputLimit


@pytest.mark.parametrize("format", ["full", "source_refs_v1", "source_names_v1"])
@pytest.mark.parametrize("field", ["entities", "assertions"])
async def test_record_overflow_requests_source_splitting(format, field):
    chunk = source()
    batch, schema = proposal(chunk)
    if format == "full":
        content = batch.model_dump(mode="json", include={"entities", "assertions"})
        maximum = 50 if field == "entities" else 100
    else:
        content = {
            "entities": [{"name": "Alpha", "type": "concept", "refs": [0]}],
            "assertions": [
                {
                    "subject": "Alpha" if format == "source_names_v1" else 0,
                    "target": "Beta" if format == "source_names_v1" else 1,
                    "predicate": "uses",
                    "polarity": "positive",
                    "confidence": 0.9,
                    "refs": [0],
                    "qualifiers": [],
                }
            ],
        }
        maximum = 30 if field == "entities" else 40
    content[field] = [content[field][0]] * (maximum + 1)
    profile, connection = configured(chunk)
    profile = profile.model_copy(update={"extraction_format": format})
    usage = []

    async def noop(_):
        pass

    async def record(value):
        usage.append(value)

    client = ExtractionClient(
        profile,
        connection,
        authorize=noop,
        record_usage=record,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps(content)}}
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 100},
                },
            )
        ),
    )
    try:
        with pytest.raises(ExtractionOutputLimit):
            await client.complete(chunk, schema)
    finally:
        await client.close()
    assert len(usage) == 1


async def test_oversized_invalid_field_is_not_a_record_overflow():
    chunk = source()
    batch, schema = proposal(chunk)
    content = batch.model_dump(mode="json", include={"entities", "assertions"})
    content["entities"][0]["name"] = "x" * 1001
    profile, connection = configured(chunk)

    async def noop(_):
        pass

    client = ExtractionClient(
        profile,
        connection,
        authorize=noop,
        record_usage=noop,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps(content)}}
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 100},
                },
            )
        ),
    )
    try:
        with pytest.raises(DomainError) as raised:
            await client.complete(chunk, schema)
    finally:
        await client.close()
    assert type(raised.value) is DomainError
    assert raised.value.code == ErrorCode.PROVIDER_UNAVAILABLE
