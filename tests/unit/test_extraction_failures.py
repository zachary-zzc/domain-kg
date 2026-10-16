"""Provider errors must survive native engine exception wrapping unchanged."""

from uuid import uuid4

import httpx
import pytest
from test_extraction_validation import proposal, source
from test_provider_extraction import configured

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.ingestion.extraction import Extractor
from domain_kg.ingestion.models import ExtractionContent
from domain_kg.providers.extraction import ExtractionClient
from domain_kg.providers.profiles import ProviderProfile


async def test_provider_failure_retains_safe_domain_code_before_native_wrapping():
    chunk = source()
    _, schema = proposal(chunk)
    failure = DomainError(ErrorCode.PROVIDER_UNAVAILABLE, "The approved provider is unavailable.")

    class Provider:
        id = uuid4()

        async def complete(self, chunk, schema):
            raise failure

    with pytest.raises(DomainError) as caught:
        await Extractor(Provider()).extract(chunk, schema)
    assert caught.value is failure


async def test_invalid_model_endpoint_reference_is_a_sanitized_evidence_failure():
    chunk = source()
    batch, schema = proposal(chunk)
    content = batch.model_dump(mode="json", include={"entities", "assertions"})
    content["assertions"][0]["target"] = "not-a-declared-local-id"

    class Provider:
        id = uuid4()

        async def complete(self, chunk, schema):
            return content

    with pytest.raises(DomainError) as caught:
        await Extractor(Provider()).extract(chunk, schema)
    assert caught.value.code == "EVIDENCE_VALIDATION_FAILED"


async def test_native_grammar_limits_do_not_change_the_canonical_evidence_schema():
    import json

    chunk = source()
    _, schema = proposal(chunk)
    approved, connection = configured(chunk)
    approved = ProviderProfile.model_validate(approved.model_dump() | {"schema_string_limit": 512})

    async def authorize(chunk):
        pass

    async def usage(value):
        pass

    def respond(request):
        body = json.loads(request.content)
        transport_schema = body["response_format"]["json_schema"]["schema"]
        assert (
            transport_schema["$defs"]["ExtractedEntity"]["properties"]["description"]["maxLength"]
            == 512
        )
        assert (
            ExtractionContent.model_json_schema()["$defs"]["ExtractedEntity"]["properties"][
                "description"
            ]["maxLength"]
            == 8000
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"entities":[],"assertions":[]}'},
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    client = ExtractionClient(
        approved,
        connection,
        authorize=authorize,
        record_usage=usage,
        transport=httpx.MockTransport(respond),
    )
    try:
        assert await client.complete(chunk, schema) == {"entities": [], "assertions": []}
    finally:
        await client.close()
