"""Provider adaptation keeps operator choices out of caller-controlled fields."""

import time
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.bridge.broker import IdentityBroker
from domain_kg.identity.bridge.config import BridgeProfile
from domain_kg.identity.oidc import OIDCVerifier


@pytest.fixture
def profile():
    return BridgeProfile(
        id=uuid4(),
        name="Synthetic provider",
        allowed_org_ids=frozenset({uuid4()}),
        caller_issuer="https://issuer.example",
        caller_audience="caller",
        caller_client_id="platform",
        caller_subject="service-platform",
        subject_issuer="https://issuer.example",
        subject_audience="assertion",
        subject_client_id="upstream",
        exchange_client_id="exchange",
        exchange_client_secret=SecretStr("synthetic-secret"),
    )


@pytest.mark.parametrize(
    "kind",
    [
        "urn:ietf:params:oauth:token-type:jwt",
        "urn:ietf:params:oauth:token-type:access_token",
    ],
)
async def test_operator_controls_exchange_token_kind_and_target(profile, monkeypatch, kind):
    seen = []

    async def metadata():
        return {"token_endpoint": "https://issuer.example/token"}

    async def verified(token):
        assert token == "synthetic-output"
        instant = int(time.time())
        return {
            "azp": "exchange",
            "jti": "unique",
            "iat": instant,
            "exp": instant + 60,
            "scope": "domain_kg:read",
            "sub": "user",
        }

    def exchange(request):
        seen.append(parse_qs(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "access_token": "synthetic-output",
                "token_type": "Bearer",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(exchange)) as http:
        canonical = OIDCVerifier("https://issuer.example", "https://kg.example/mcp", http)
        monkeypatch.setattr(canonical, "metadata", metadata)
        monkeypatch.setattr(canonical, "verify_claims", verified)
        broker = IdentityBroker(canonical, http)
        selected = profile.model_copy(
            update={
                "exchange_subject_token_type": kind,
                "exchange_parameter": "audience",
                "exchange_audience": "approved-target",
            }
        )
        token, _ = await broker.exchange(selected, "synthetic-user", ("domain_kg:read",))
    assert token == "synthetic-output"
    assert seen[0]["subject_token_type"] == [kind]
    assert seen[0]["audience"] == ["approved-target"]
    assert "resource" not in seen[0]


async def test_discovery_failure_uses_the_same_safe_provider_error(profile, monkeypatch):
    async def unavailable():
        raise httpx.ConnectError("private provider detail")

    async with httpx.AsyncClient() as http:
        canonical = OIDCVerifier("https://issuer.example", "https://kg.example/mcp", http)
        monkeypatch.setattr(canonical, "metadata", unavailable)
        with pytest.raises(DomainError) as failed:
            await IdentityBroker(canonical, http).exchange(
                profile, "synthetic-user", ("domain_kg:read",)
            )
    assert failed.value.code == ErrorCode.BACKEND_UNAVAILABLE
    assert failed.value.retryable
    assert "private provider detail" not in str(failed.value)


@pytest.mark.parametrize(
    "field",
    [
        "caller_audience",
        "caller_client_id",
        "caller_subject",
        "subject_audience",
        "subject_client_id",
        "exchange_client_id",
        "subject_issuer_alias",
    ],
)
@pytest.mark.parametrize("invalid", ["", "   "])
def test_profiles_reject_empty_identity_boundaries(profile, field, invalid):
    with pytest.raises(ValidationError):
        BridgeProfile.model_validate(profile.model_dump() | {field: invalid})


@pytest.mark.parametrize(
    "field,maximum",
    [
        ("caller_audience", 2048),
        ("subject_audience", 2048),
        ("caller_client_id", 512),
        ("caller_subject", 512),
        ("subject_client_id", 512),
        ("exchange_client_id", 512),
    ],
)
def test_profiles_bound_identity_configuration_sizes(profile, field, maximum):
    with pytest.raises(ValidationError):
        BridgeProfile.model_validate(profile.model_dump() | {field: "x" * (maximum + 1)})


async def test_oversized_idp_reply_is_stopped_before_full_download(profile, monkeypatch):
    consumed = []

    class Oversized(httpx.AsyncByteStream):
        async def __aiter__(self):
            for index in range(100):
                consumed.append(index)
                yield b"x" * 16384

    async def metadata():
        return {"token_endpoint": "https://issuer.example/token"}

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Oversized()))
    ) as http:
        canonical = OIDCVerifier("https://issuer.example", "https://kg.example/mcp", http)
        monkeypatch.setattr(canonical, "metadata", metadata)
        with pytest.raises(DomainError) as failure:
            await IdentityBroker(canonical, http).exchange(
                profile, "assertion", ("domain_kg:read",)
            )
    assert failure.value.code == ErrorCode.BACKEND_UNAVAILABLE
    assert len(consumed) <= 5
