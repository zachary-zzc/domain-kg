"""Approved endpoint only, untrusted data, bounded structured response and revocation."""

import json
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from test_extraction_validation import proposal, source

from domain_kg.errors import DomainError
from domain_kg.providers.extraction import ExtractionClient
from domain_kg.providers.profiles import ProviderConnection, ProviderProfile, ProviderRegistry


def configured(chunk):
    from domain_kg.models import Namespace

    profile = ProviderProfile(
        id=uuid4(),
        connection_ref="approved",
        extraction_model="approved-test-model",
        embedding_model="approved-test-embedding",
        embedding_dimensions=3,
        allowed_namespaces=(Namespace(org_id=chunk.org_id, kg_id=chunk.kg_id),),
        data_policy_approved=True,
    )
    connection = ProviderConnection(
        ref="approved", endpoint="https://provider.example/v1", api_key=SecretStr("test-secret")
    )
    return profile, connection


async def test_strict_request_usage_and_post_response_authorization():
    chunk = source()
    batch, schema = proposal(chunk)
    profile, connection = configured(chunk)
    authorizations, usage = [], []

    async def authorize(item):
        assert item == chunk
        authorizations.append(item.id)

    async def record(item):
        usage.append(item)

    def respond(request):
        body = json.loads(request.content)
        assert body["store"] is False and "tools" not in body
        assert body["model"] == "approved-test-model"
        assert str(chunk.org_id) in body["messages"][0]["content"]
        shape = body["response_format"]["json_schema"]
        assert shape["strict"] is True
        assert shape["schema"]["additionalProperties"] is False
        assert shape["schema"]["required"] == ["entities", "assertions"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                batch.model_dump(mode="json", include={"entities", "assertions"})
                            )
                        },
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 25},
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
        assert len(result["entities"]) == 2 and len(authorizations) == 2
        assert usage[0].input_tokens == 100 and usage[0].kg_id == chunk.kg_id

        async def revoked(_):
            if len(authorizations) >= 3:
                raise DomainError("FORBIDDEN", "Access changed.")
            authorizations.append(chunk.id)

        client.authorize = revoked
        with pytest.raises(DomainError):
            await client.complete(chunk, schema)
    finally:
        await client.close()


@pytest.mark.parametrize(
    "failure", ["refusal", "length", "tools", "invalid", "oversize", "redirect"]
)
async def test_invalid_provider_response_is_a_safe_failure(failure):
    chunk = source()
    _, schema = proposal(chunk)
    profile, connection = configured(chunk)

    async def noop(_):
        pass

    def respond(_):
        if failure == "oversize":
            return httpx.Response(200, content=b"x" * (2 * 1024**2 + 1))
        if failure == "redirect":
            return httpx.Response(302, headers={"location": "https://unapproved.example/"})
        message = {"content": '{"entities":[],"assertions":[]}'}
        if failure == "refusal":
            message["refusal"] = "private upstream error"
        if failure == "tools":
            message["tool_calls"] = [{"function": "database_command"}]
        if failure == "invalid":
            message["content"] = '{"entities":[],"assertions":[],"permissions":"owner"}'
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "length" if failure == "length" else "stop",
                        "message": message,
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    client = ExtractionClient(
        profile,
        connection,
        authorize=noop,
        record_usage=noop,
        transport=httpx.MockTransport(respond),
    )
    try:
        with pytest.raises(DomainError) as error:
            await client.complete(chunk, schema)
        assert "private upstream" not in str(error.value)
    finally:
        await client.close()


def test_profile_is_fail_closed_and_no_remote_plaintext_or_credentials_in_repr():
    chunk = source()
    profile, connection = configured(chunk)
    assert "test-secret" not in repr(connection)
    with pytest.raises(ValidationError):
        ProviderConnection(ref="bad", endpoint="http://remote.example/v1", api_key="x")
    with pytest.raises(DomainError):
        ProviderRegistry().resolve(profile.id, profile.allowed_namespaces[0], restricted=False)


async def test_truncated_output_is_rejected_but_its_actual_usage_is_counted():
    chunk = source()
    _, schema = proposal(chunk)
    profile, connection = configured(chunk)
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
                        {"finish_reason": "length", "message": {"content": '{"entities":['}}
                    ],
                    "usage": {"prompt_tokens": 123, "completion_tokens": 512},
                },
            )
        ),
    )
    try:
        with pytest.raises(DomainError):
            await client.complete(chunk, schema)
        assert len(usage) == 1 and usage[0].input_tokens == 123 and usage[0].output_tokens == 512
    finally:
        await client.close()
