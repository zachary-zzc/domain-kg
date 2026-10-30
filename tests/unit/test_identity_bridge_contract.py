"""Public examples and generated schema exercise the same current API contracts."""

import json
from uuid import uuid4

import httpx
import pytest

from domain_kg.identity.models import Principal
from examples.identity_bridge_client import BridgeFailure, KGIdentityClient
from scripts.export_identity_openapi import OUTPUT, contract


def test_external_openapi_is_current_and_no_internal_secrets_are_public():
    schema = contract()
    assert json.loads(OUTPUT.read_text()) == schema
    assert (
        len(schema["paths"]) == 12
    )  # The integration collection has both GET and POST operations.
    assert "subject_token" in schema["components"]["schemas"]["AssertionRequest"]["properties"]
    assert "exchange_client_secret" not in json.dumps(schema)
    assert "response_key" not in json.dumps(schema)
    operation = schema["paths"]["/api/v1/identity-bridge/token"]["post"]
    assert operation["security"] == [{"BearerCredential": []}]
    assert any(p["name"] == "Idempotency-Key" and p["required"] for p in operation["parameters"])
    assert operation["responses"]["422"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/IdentityError"
    )


def test_legacy_principal_payload_omits_new_optional_delegation():
    principal = Principal(issuer="https://idp.example", subject="legacy")
    assert principal.model_dump(mode="json") == {
        "issuer": "https://idp.example",
        "subject": "legacy",
        "scopes": [],
        "kind": "human",
    }


async def test_public_client_preserves_retry_keys_and_redacts_failed_response():
    integration = uuid4()
    calls = []

    def reply(request):
        calls.append(request)
        if request.url.path.endswith("/token"):
            return httpx.Response(
                503,
                json={
                    "code": "BACKEND_UNAVAILABLE",
                    "retryable": True,
                    "request_id": "safe-id",
                    "private": "secret-token",
                },
            )
        return httpx.Response(200, json={"member_id": str(uuid4()), "status": "active"})

    async with httpx.AsyncClient(
        base_url="https://kg.example", transport=httpx.MockTransport(reply)
    ) as http:
        client = KGIdentityClient(http, "private-platform-token", integration)
        await client.bootstrap("private-assertion", "durable-key")
        request = calls[0]
        assert request.headers["Idempotency-Key"] == "durable-key"
        assert request.headers["Authorization"] == "Bearer private-platform-token"
        assert json.loads(request.content)["integration_id"] == str(integration)
        with pytest.raises(BridgeFailure) as failure:
            await client.exchange("fresh-assertion", "other-key", (uuid4(),))
        assert failure.value.retryable and failure.value.request_id == "safe-id"
        assert "secret-token" not in str(failure.value)
        assert "private-platform-token" not in repr(client)
