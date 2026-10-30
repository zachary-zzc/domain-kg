import json

import httpx
import pytest
from test_extraction_validation import proposal, source
from test_provider_extraction import configured

from domain_kg.errors import DomainError
from domain_kg.providers.extraction import ExtractionClient
from domain_kg.providers.profiles import ProviderConnection, ProviderProfile


async def test_native_claude_uses_messages_auth_and_preserves_source_validation():
    chunk = source()
    batch, schema = proposal(chunk)
    profile, connection = configured(chunk)
    connection = ProviderConnection.model_validate(
        connection.model_dump() | {"api_format": "anthropic"}
    )
    usage = []

    async def authorize(item):
        assert item == chunk

    async def record(item):
        usage.append(item)

    def respond(request):
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "test-secret"
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert "authorization" not in request.headers
        body = json.loads(request.content)
        assert str(chunk.org_id) in body["system"]
        assert body["messages"][0]["role"] == "user"
        assert body["output_config"]["format"]["type"] == "json_schema"
        assert body["max_tokens"] == profile.max_completion_tokens
        assert "tools" not in body and "store" not in body
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            batch.model_dump(mode="json", include={"entities", "assertions"})
                        ),
                    }
                ],
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": 120, "output_tokens": 30,
                    "cache_creation_input_tokens": 10, "cache_read_input_tokens": 20,
                },
            },
        )

    client = ExtractionClient(
        profile,
        connection,
        authorize=authorize,
        record_usage=record,
        transport=httpx.MockTransport(respond),
    )
    try:
        result = await client.complete(chunk, schema)
        assert len(result["entities"]) == 2
        assert usage[0].input_tokens == 150 and usage[0].output_tokens == 30
    finally:
        await client.close()


@pytest.mark.parametrize(
    "status,body,code,retryable",
    [
        (
            401,
            {"error": {"message": "private credential"}},
            "PROVIDER_AUTHENTICATION_FAILED",
            False,
        ),
        (402, {"error": {"type": "billing_error"}}, "PROVIDER_BILLING_REQUIRED", False),
        (429, {"error": {"code": "insufficient_quota"}}, "PROVIDER_QUOTA_EXCEEDED", False),
        (
            400,
            {"code": "Arrearage", "message": "private balance"},
            "PROVIDER_BILLING_REQUIRED",
            False,
        ),
        (429, {"error": {"type": "rate_limit_error"}}, "PROVIDER_RATE_LIMITED", True),
        (404, {"error": {"message": "private model"}}, "PROVIDER_MODEL_NOT_FOUND", False),
    ],
)
async def test_real_extraction_errors_explain_billing_auth_and_limits_safely(
    status, body, code, retryable
):
    chunk = source()
    _, schema = proposal(chunk)
    profile, connection = configured(chunk)

    async def noop(_):
        pass

    client = ExtractionClient(
        profile,
        connection,
        authorize=noop,
        record_usage=noop,
        transport=httpx.MockTransport(lambda _: httpx.Response(status, json=body)),
    )
    try:
        with pytest.raises(DomainError) as error:
            await client.complete(chunk, schema)
        assert str(error.value.code) == code
        assert error.value.retryable == retryable
        assert "private" not in error.value.message
    finally:
        await client.close()


def test_legacy_connection_serialization_is_unchanged():
    _, connection = configured(source())
    assert "api_format" not in connection.model_dump()
    profile, _ = configured(source())
    assert "embedding_send_dimensions" not in profile.model_dump()


def test_anthropic_cannot_be_used_as_an_embedding_connection():
    from domain_kg.models import Namespace
    from domain_kg.providers.profiles import ProviderConfig, ProviderRegistry

    chunk = source()
    profile, connection = configured(chunk)
    native = ProviderConnection.model_validate(
        connection.model_dump() | {"api_format": "anthropic"}
    )
    registry = ProviderRegistry(ProviderConfig(profiles=(profile,), connections=(native,)))
    with pytest.raises(DomainError):
        registry.resolve_embedding(
            profile.id, Namespace(org_id=chunk.org_id, kg_id=chunk.kg_id), restricted=False
        )


async def test_openai_embedding_requests_the_configured_dimension():
    from domain_kg.providers.embeddings import EmbeddingClient, EmbeddingProfile

    chunk = source()
    approved, connection = configured(chunk)
    approved = ProviderProfile.model_validate(
        approved.model_dump() | {"embedding_send_dimensions": True}
    )
    profile = EmbeddingProfile(
        org_id=chunk.org_id,
        kg_id=chunk.kg_id,
        id=approved.id,
        revision=approved.revision,
        model=approved.embedding_model,
        dimensions=3,
    )

    async def authorize():
        pass

    async def record(_):
        pass

    def respond(request):
        assert json.loads(request.content)["dimensions"] == 3
        return httpx.Response(
            200,
            json={
                "model": approved.embedding_model,
                "data": [{"index": 0, "embedding": [1, 0, 0]}],
                "usage": {"prompt_tokens": 1},
            },
        )

    client = EmbeddingClient(
        approved,
        connection,
        authorize=authorize,
        record_usage=record,
        transport=httpx.MockTransport(respond),
    )
    try:
        assert await client.embed(["hello"], profile) == [[1.0, 0.0, 0.0]]
    finally:
        await client.close()
