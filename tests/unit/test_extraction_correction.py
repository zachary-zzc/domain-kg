"""One evidence correction never relaxes validation, context bounds or authorization."""

import json
from uuid import uuid4

import httpx
import pytest
from test_extraction_validation import source
from test_provider_extraction import configured

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.schema import schema_for
from domain_kg.ingestion.models import SourceLocation, Span
from domain_kg.models import Namespace
from domain_kg.providers.extraction import ExtractionClient, extraction_payload


def fixture():
    texts = ["Alpha supports the following project:", "Beta."]
    chunk = source().model_copy(
        update={
            "text": "\n".join(texts),
            "spans": tuple(
                Span(
                    location=SourceLocation(kind="text", block_id=uuid4(), paragraph=i),
                    start=0,
                    end=len(text),
                    quote=text,
                )
                for i, text in enumerate(texts)
            ),
        }
    )
    profile, connection = configured(chunk)
    profile = profile.model_copy(update={"extraction_format": "source_refs_v1"})
    schema = schema_for(Namespace(org_id=chunk.org_id, kg_id=chunk.kg_id), "general")
    content = {
        "entities": [
            {"name": "Alpha", "type": "concept", "refs": [0]},
            {"name": "Beta", "type": "concept", "refs": [1]},
        ],
        "assertions": [
            {
                "subject": 0,
                "target": 1,
                "predicate": "supports",
                "polarity": "positive",
                "confidence": 0.9,
                "refs": [1],
                "qualifiers": [],
            }
        ],
    }
    return chunk, profile, connection, schema, content


@pytest.mark.parametrize("repair", [True, False])
async def test_evidence_failure_gets_one_corrective_request_and_still_requires_both_names(repair):
    chunk, profile, connection, schema, content = fixture()
    calls, usage = [], []

    def respond(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) == 2:
            assert (
                "The relation evidence does not contain both endpoint names."
                in payload["messages"][-1]["content"]
            )
            if repair:
                content["assertions"][0]["refs"] = [0, 1]
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 20},
            },
        )

    async def authorize(_):
        pass

    async def record(value):
        usage.append(value)

    client = ExtractionClient(
        profile,
        connection,
        authorize=authorize,
        record_usage=record,
        transport=httpx.MockTransport(respond),
    )
    try:
        if repair:
            result = await client.complete(chunk, schema)
            assert len(result["assertions"]) == 1
            assert len(result["assertions"][0]["spans"]) == 2
        else:
            result = await client.complete(chunk, schema)
            assert len(result["entities"]) == 2
            assert result["assertions"] == []
            assert result["warnings"] == ["extraction_rejected_assertions:1"]
        assert len(calls) == len(usage) == 2
    finally:
        await client.close()


async def test_partial_result_keeps_valid_relationships_and_rejects_whole_bad_claims():
    import copy

    from domain_kg.ingestion.extraction import Extractor, validate_extraction

    chunk, profile, connection, schema, content = fixture()
    good = copy.deepcopy(content["assertions"][0])
    good["refs"] = [0, 1]
    unknown = copy.deepcopy(good)
    unknown["subject"] = 99
    bad_qualifier = copy.deepcopy(good)
    bad_qualifier["qualifiers"] = [{"name": "time", "value": "Invented", "refs": [0]}]
    content["assertions"] = [good, content["assertions"][0], unknown, bad_qualifier]
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 20},
            },
        )

    async def noop(_):
        pass

    client = ExtractionClient(
        profile,
        connection,
        authorize=noop,
        record_usage=noop,
        transport=httpx.MockTransport(respond),
    )
    try:
        result = await Extractor(client).extract(chunk, schema)
        assert len(result.entities) == 2 and len(result.assertions) == 1
        assert result.warnings == ("extraction_rejected_assertions:3",)
        assert validate_extraction(chunk, schema, result) == result
        assert len(calls) == 2
    finally:
        await client.close()


async def test_correction_that_cannot_fit_the_context_sends_no_oversize_request():
    chunk, profile, connection, schema, content = fixture()
    payload = extraction_payload(chunk, schema, profile)
    size = len(
        json.dumps(
            {"messages": payload["messages"], "response_format": payload["response_format"]},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    )
    profile = profile.model_copy(
        update={"extraction_context_tokens": size + profile.max_completion_tokens + 256}
    )
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 20},
            },
        )

    async def noop(_):
        pass

    client = ExtractionClient(
        profile,
        connection,
        authorize=noop,
        record_usage=noop,
        transport=httpx.MockTransport(respond),
    )
    try:
        with pytest.raises(DomainError) as rejected:
            await client.complete(chunk, schema)
        assert rejected.value.code == ErrorCode.EVIDENCE_VALIDATION_FAILED
        assert len(calls) == 1
    finally:
        await client.close()


@pytest.mark.parametrize("output_format", ["json_schema", "json_object"])
async def test_prepared_windows_reserve_room_for_the_evidence_correction(output_format):
    from domain_kg.providers.budget import extraction_request_fits, fit_source_chunks

    chunk, profile, connection, schema, _ = fixture()
    spans = tuple(
        span.model_copy(update={"quote": span.quote * 100, "end": len(span.quote) * 100})
        for span in chunk.spans
    )
    chunk = chunk.model_copy(update={"text": "\n".join(s.quote for s in spans), "spans": spans})
    profile = profile.model_copy(update={"extraction_response_format": output_format})
    payload = extraction_payload(chunk, schema, profile)
    size = len(
        json.dumps(
            {"messages": payload["messages"], "response_format": payload["response_format"]},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    )
    profile = profile.model_copy(
        update={"extraction_context_tokens": size + profile.max_completion_tokens + 256}
    )
    windows = fit_source_chunks([chunk], schema, profile)
    calls = []

    def respond(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert extraction_request_fits(payload, profile)
        content = (
            {"entities": [], "assertions": []}
            if len(payload["messages"]) == 3
            else {
                "entities": [{"name": "Invented", "type": "concept", "refs": [0]}],
                "assertions": [],
            }
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 20},
            },
        )

    async def noop(_):
        pass

    client = ExtractionClient(
        profile,
        connection,
        authorize=noop,
        record_usage=noop,
        transport=httpx.MockTransport(respond),
    )
    try:
        for window in windows:
            assert await client.complete(window, schema) == {"entities": [], "assertions": []}
        assert len(calls) == 2 * len(windows)
    finally:
        await client.close()


async def test_access_revocation_before_correction_sends_no_second_request():
    chunk, profile, connection, schema, content = fixture()
    calls, checks = [], []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 20},
            },
        )

    async def authorize(_):
        checks.append(1)
        if len(checks) == 3:
            raise DomainError(ErrorCode.FORBIDDEN, "Access revoked.")

    async def noop(_):
        pass

    client = ExtractionClient(
        profile,
        connection,
        authorize=authorize,
        record_usage=noop,
        transport=httpx.MockTransport(respond),
    )
    try:
        with pytest.raises(DomainError) as rejected:
            await client.complete(chunk, schema)
        assert rejected.value.code == ErrorCode.FORBIDDEN
        assert len(calls) == 1
    finally:
        await client.close()


async def test_full_format_dangling_endpoint_uses_the_same_bounded_correction():
    from test_extraction_validation import proposal

    chunk = source()
    batch, schema = proposal(chunk)
    profile, connection = configured(chunk)
    calls = []

    def respond(request):
        calls.append(request)
        content = batch.model_dump(mode="json", include={"entities", "assertions"})
        if len(calls) == 1:
            content["assertions"][0]["subject"] = "unknown"
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 20},
            },
        )

    async def noop(_):
        pass

    client = ExtractionClient(
        profile,
        connection,
        authorize=noop,
        record_usage=noop,
        transport=httpx.MockTransport(respond),
    )
    try:
        result = await client.complete(chunk, schema)
        assert result["assertions"][0]["subject"] == batch.entities[0].local_id
        assert len(calls) == 2
    finally:
        await client.close()
