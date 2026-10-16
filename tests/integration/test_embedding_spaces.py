"""Real acceptance fences reject silent provider/vector-space changes."""

from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError
from domain_kg.identity.models import Action
from domain_kg.models import Namespace
from domain_kg.providers.profiles import (
    ProviderConfig,
    ProviderConnection,
    ProviderProfile,
    ProviderRegistry,
)
from domain_kg.providers.service import ProviderService

pytestmark = pytest.mark.storage


async def test_same_model_name_on_another_endpoint_requires_a_new_generation(publishing, team):  # noqa: F811
    materials, _, queue, coordinator, _, _ = publishing
    _, job, checkpoint = await prepare(publishing)
    await coordinator.publish(publishing[1], await ready(publishing, job, checkpoint))
    current = next(iter(coordinator.providers.registry.profiles.values()))
    original = next(iter(coordinator.providers.registry.connections.values()))
    other = current.model_copy(update={"id": uuid4(), "connection_ref": "other"})
    registry = ProviderRegistry(
        ProviderConfig(
            profiles=(current, other),
            connections=(
                original,
                ProviderConnection(
                    ref="other",
                    endpoint="https://another-embedding.example/v1",
                    api_key=SecretStr("fixture"),
                ),
            ),
        )
    )
    service = ProviderService(team[1], materials.router, registry)
    owner = await team[1].resolve_access(team[2]["owner"], [job.kg_id], Action.CONFIGURE)
    with pytest.raises(DomainError) as error:
        await service.bind(
            owner, job.kg_id, other.id, expected_revision=1, idempotency_key="different-space"
        )
    assert error.value.code == "VERSION_CONFLICT"
    assert (await service.selected(owner, job.kg_id))["profile_id"] == str(current.id)
    original_space = (await service.selected(owner, job.kg_id))["embedding_space_id"]
    compatible = current.model_copy(
        update={
            "id": uuid4(),
            "connection_ref": "other",
            "embedding_connection_ref": original.ref,
            "extraction_model": "different-chat-model",
        }
    )
    service.registry = ProviderRegistry(
        ProviderConfig(
            profiles=(current, other, compatible),
            connections=tuple(registry.connections.values()),
        )
    )
    assert (
        await service.bind(
            owner, job.kg_id, compatible.id, expected_revision=1, idempotency_key="chat-only-change"
        )
        == 2
    )
    selected = await service.selected(owner, job.kg_id)
    assert selected["embedding_space_id"] == original_space
    from domain_kg.retrieval.models import SearchRequest
    from domain_kg.retrieval.vector import VectorRetrievalBranch

    service.transport = coordinator.providers.transport
    reader = await team[1].resolve_access(team[2]["reader"], [job.kg_id], Action.SEARCH)
    branch = VectorRetrievalBranch(queue, service)
    request = SearchRequest(kg_ids=(job.kg_id,), query="Alpha", mode="vector")
    scope = await branch.resolver.resolve(reader, request, job.kg_id)
    assert await branch.retrieve(scope, request)


async def test_embedding_calls_use_the_separate_approved_connection(material_service, team):
    materials, kg = material_service
    namespace = Namespace(org_id=team[4], kg_id=kg)
    profile = ProviderProfile(
        id=uuid4(),
        connection_ref="chat",
        embedding_connection_ref="embedding",
        extraction_model="domain-kg-small-llm",
        embedding_model="embedding-fixture",
        embedding_dimensions=3,
        data_policy_approved=True,
        allowed_namespaces=(namespace,),
    )
    registry = ProviderRegistry(
        ProviderConfig(
            profiles=(profile,),
            connections=(
                ProviderConnection(
                    ref="chat", endpoint="http://127.0.0.1:29500/v1", api_key=SecretStr("mock-chat")
                ),
                ProviderConnection(
                    ref="embedding",
                    endpoint="https://embedding.example/v1",
                    api_key=SecretStr("mock-embedding"),
                ),
            ),
        )
    )
    calls = []

    def respond(request):
        calls.append(request.url.host)
        assert request.url.host == "embedding.example"
        assert request.headers["authorization"] == "Bearer mock-embedding"
        return httpx.Response(
            200,
            json={
                "model": profile.embedding_model,
                "data": [{"index": 0, "embedding": [1.0, 0.0, 0.0]}],
                "usage": {"prompt_tokens": 2},
            },
        )

    service = ProviderService(
        team[1], materials.router, registry, transport=httpx.MockTransport(respond)
    )
    owner = await team[1].resolve_access(team[2]["owner"], [kg], Action.CONFIGURE)
    await service.bind(owner, kg, profile.id, expected_revision=0, idempotency_key="split-provider")
    reader = await team[1].resolve_access(team[2]["reader"], [kg], Action.SEARCH)
    embedded_profile, vectors = await service.embeddings(reader, namespace, ["query"])
    assert vectors == [[1.0, 0.0, 0.0]] and calls == ["embedding.example"]
    assert embedded_profile.space_id == (await service.selected(reader, kg))["embedding_space_id"]
