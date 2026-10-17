"""Numbered evidence reduces model work without weakening canonical validation."""

import json

import httpx
import pytest
from test_extraction_validation import proposal, source
from test_provider_extraction import configured

from domain_kg.errors import DomainError
from domain_kg.ingestion.models import ExtractionContent
from domain_kg.providers.budget import extraction_fits
from domain_kg.providers.extraction import ExtractionClient, extraction_payload
from domain_kg.providers.profiles import ProviderProfile


def compact_profile(chunk):
    profile, connection = configured(chunk)
    profile = ProviderProfile.model_validate(
        profile.model_copy(
            update={
                "extraction_format": "source_refs_v1",
                "extraction_context_tokens": 4096,
                "max_completion_tokens": 1024,
                "schema_in_context": False,
            }
        )
    )
    return profile, connection


def facts():
    return {
        "entities": [
            {"name": "Alpha", "type": "concept", "refs": [0]},
            {"name": "Beta", "type": "concept", "refs": [0]},
        ],
        "assertions": [
            {
                "subject": 0,
                "target": 1,
                "predicate": "uses",
                "polarity": "positive",
                "confidence": 0.9,
                "refs": [0],
                "qualifiers": [],
            }
        ],
    }


async def call(chunk, value, *, authorize=None):
    profile, connection = compact_profile(chunk)
    _, schema = proposal(chunk)
    usage = []

    async def noop(_):
        pass

    async def record(item):
        usage.append(item)

    client = ExtractionClient(
        profile,
        connection,
        authorize=authorize or noop,
        record_usage=record,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps(value)}}
                    ],
                    "usage": {"prompt_tokens": 120, "completion_tokens": 100},
                },
            )
        ),
    )
    try:
        result = ExtractionContent.model_validate(await client.complete(chunk, schema))
        return result, usage
    finally:
        await client.close()


async def test_compact_request_fits_small_model_and_restores_exact_source_locations():
    chunk = source("Alpha uses Beta.")
    profile, _ = compact_profile(chunk)
    _, schema = proposal(chunk)
    assert extraction_fits(chunk, schema, profile)
    request = extraction_payload(chunk, schema, profile)
    data = json.loads(request["messages"][1]["content"])
    assert data["sources"] == [{"id": 0, "text": "Alpha uses Beta."}]
    assert "tools" not in request and str(chunk.org_id) in request["messages"][0]["content"]
    result, usage = await call(chunk, facts())
    assert [e.name for e in result.entities] == ["Alpha", "Beta"]
    assert result.assertions[0].subject == result.entities[0].local_id
    assert result.assertions[0].target == result.entities[1].local_id
    assert result.assertions[0].spans == chunk.spans
    assert result.entities[0].spans == chunk.spans
    assert usage[0].input_tokens == 120


@pytest.mark.parametrize(
    "bad",
    [
        "foreign_ref",
        "negative_ref",
        "bool_ref",
        "invented_name",
        "predicate",
        "type",
        "endpoint",
        "duplicate",
        "command",
        "positive_negation",
    ],
)
async def test_bad_compact_fields_cannot_be_accepted(bad):
    chunk, value = source("Alpha uses Beta."), facts()
    if bad == "foreign_ref":
        value["entities"][0]["refs"] = [999]
    if bad == "negative_ref":
        value["entities"][0]["refs"] = [-1]
    if bad == "bool_ref":
        value["entities"][0]["refs"] = [False]
    if bad == "invented_name":
        value["entities"][0]["name"] = "Invented"
    if bad == "predicate":
        value["assertions"][0]["predicate"] = "grant_owner"
    if bad == "type":
        value["entities"][0]["type"] = "admin"
    if bad == "endpoint":
        value["assertions"][0]["target"] = 99
    if bad == "duplicate":
        value["entities"][1]["name"] = "Alpha"
    if bad == "command":
        value["commands"] = ["MATCH (n) DELETE n"]
    if bad == "positive_negation":
        chunk = source("Alpha does not use Beta.")
    with pytest.raises(DomainError):
        await call(chunk, value)


async def test_compact_preserves_negation_qualifiers_and_post_call_revocation():
    chunk = source("Alpha does not use Beta during winter.")
    value = facts()
    value["assertions"][0].update(
        polarity="negative", qualifiers=[{"name": "time", "value": "winter", "refs": [0]}]
    )
    result, _ = await call(chunk, value)
    assert result.assertions[0].polarity == "negative"
    assert result.assertions[0].qualifiers[0].value == "winter"
    count = 0

    async def revoke(_):
        nonlocal count
        count += 1
        if count == 2:
            raise DomainError("FORBIDDEN", "Revoked")

    with pytest.raises(DomainError):
        await call(chunk, value, authorize=revoke)
