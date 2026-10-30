"""Private model configuration is atomic, live-reloaded, and retry-safe."""

import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_kg.errors import DomainError
from domain_kg.models import Namespace
from domain_kg.providers.configuration import ModelConfiguration, ProfileFile
from domain_kg.providers.profiles import ProviderConfig, ProviderRegistry


def private_file(tmp_path):
    path = tmp_path / "providers.json"
    path.write_text(ProviderConfig().model_dump_json())
    path.chmod(0o600)
    return path


def command(**kwargs):
    return ModelConfiguration(
        name="Local model",
        endpoint="http://127.0.0.1:1234/v1",
        api_key="",
        extraction_model="local-chat",
        embedding_model="local-embed",
        data_approval="approved",
        expected_revision=0,
        idempotency_key="local-model",
        **kwargs,
    )


def test_registration_is_private_atomic_idempotent_and_worker_reloads(tmp_path):
    path = private_file(tmp_path)
    worker = ProviderRegistry.from_file(path)
    namespace = Namespace(org_id=uuid4(), kg_id=uuid4())
    store = ProfileFile(path)
    identifier = uuid4()
    assert store.add(identifier, namespace, command(), dimensions=1024) == identifier
    assert store.add(identifier, namespace, command(), dimensions=1024) == identifier
    profile, connection = worker.resolve(identifier, namespace, restricted=False)
    assert profile.embedding_dimensions == 1024 and profile.name == "Local model"
    assert connection.endpoint == "http://127.0.0.1:1234/v1"
    assert path.stat().st_mode & 0o077 == 0
    assert len(json.loads(path.read_text())["profiles"]) == 1
    with pytest.raises(DomainError):
        store.add(
            identifier,
            namespace,
            command().model_copy(update={"name": "Different"}),
            dimensions=1024,
        )
    with pytest.raises(DomainError):
        worker.resolve(
            identifier, Namespace(org_id=namespace.org_id, kg_id=uuid4()), restricted=False
        )
    # Losing access to the private configuration must not silently retain stale approval.
    path.chmod(0o644)
    with pytest.raises(DomainError):
        worker.resolve(identifier, namespace, restricted=False)


def test_separate_embedding_endpoint_and_secret_roundtrip(tmp_path):
    path = private_file(tmp_path)
    namespace = Namespace(org_id=uuid4(), kg_id=uuid4())
    config = command(
        embedding_endpoint="https://embed.example/v1",
        embedding_api_key="private-embed-key",
        thinking="disabled",
    )
    identifier = uuid4()
    ProfileFile(path).add(identifier, namespace, config, dimensions=1024)
    profile, connection = ProviderRegistry.from_file(path).resolve_embedding(
        identifier, namespace, restricted=False
    )
    assert connection.endpoint == "https://embed.example/v1"
    assert profile.extraction_enable_thinking is False
    assert connection.api_key.get_secret_value() == "private-embed-key"
    assert "private-embed-key" not in repr(config)


def test_concurrent_saves_preserve_all_profiles_and_original_budget(tmp_path):
    path = private_file(tmp_path)
    namespace = Namespace(org_id=uuid4(), kg_id=uuid4())
    identifiers = [uuid4() for _ in range(4)]
    store = ProfileFile(path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(
            pool.map(
                lambda identifier: store.add(identifier, namespace, command(), dimensions=1024),
                identifiers,
            )
        )
    result = ProviderConfig.model_validate_json(path.read_bytes())
    assert {item.id for item in result.profiles} == set(identifiers)
    assert result.budgets == ProviderConfig().budgets


def test_approval_stays_in_organization_and_preserves_original_retry(tmp_path):
    path = private_file(tmp_path)
    namespace = Namespace(org_id=uuid4(), kg_id=uuid4())
    second = Namespace(org_id=namespace.org_id, kg_id=uuid4())
    store, identifier = ProfileFile(path), uuid4()
    store.add(identifier, namespace, command(), dimensions=1024)
    with pytest.raises(DomainError):
        store.approve(Namespace(org_id=uuid4(), kg_id=uuid4()), identifier, dimensions=1024)
    with pytest.raises(DomainError):
        store.approve(second, identifier, dimensions=768)
    store.approve(second, identifier, dimensions=1024)
    store.add(identifier, namespace, command(), dimensions=1024)
    profile, _ = ProviderRegistry.from_file(path).resolve(identifier, second, restricted=False)
    assert profile.allowed_namespaces == (namespace, second)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://remote.example/v1",
        "https://user:pass@example.com/v1",
        "https://example.com/v1?key=secret",
        "https://example.com/v1#secret",
    ],
)
def test_reject_unsafe_endpoint_forms(endpoint):
    with pytest.raises(ValidationError):
        command().model_validate(command().model_dump() | {"endpoint": endpoint})


async def test_keyless_local_clients_omit_authorization():
    import httpx
    from pydantic import SecretStr
    from test_extraction_validation import proposal, source
    from test_provider_extraction import configured

    from domain_kg.providers.embeddings import EmbeddingClient, EmbeddingProfile
    from domain_kg.providers.extraction import ExtractionClient

    chunk = source()
    approved, connection = configured(chunk)
    connection = connection.model_copy(
        update={"endpoint": "http://127.0.0.1:1234/v1", "api_key": SecretStr("")}
    )
    batch, schema = proposal(chunk)
    calls = []

    def respond(request):
        assert "authorization" not in request.headers
        calls.append(request.url.path)
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(
                200,
                json={
                    "model": approved.embedding_model,
                    "data": [{"index": 0, "embedding": [1.0, 0.0, 0.0]}],
                    "usage": {"prompt_tokens": 2, "total_tokens": 2},
                },
            )
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

    async def authorize(*_):
        pass

    async def usage(_):
        pass

    transport = httpx.MockTransport(respond)
    extractor = ExtractionClient(
        approved, connection, authorize=authorize, record_usage=usage, transport=transport
    )
    embedding = EmbeddingClient(
        approved, connection, authorize=authorize, record_usage=usage, transport=transport
    )
    try:
        assert (await extractor.complete(chunk, schema))["entities"]
        profile = EmbeddingProfile(
            org_id=chunk.org_id,
            kg_id=chunk.kg_id,
            id=approved.id,
            revision=approved.revision,
            model=approved.embedding_model,
            dimensions=3,
        )
        assert await embedding.embed(["hello"], profile) == [[1.0, 0.0, 0.0]]
        assert calls == ["/v1/chat/completions", "/v1/embeddings"]
    finally:
        await extractor.close()
        await embedding.close()
