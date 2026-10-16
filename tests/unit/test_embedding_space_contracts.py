"""Embedding-space contracts do not rely on model names alone."""

from uuid import uuid4

from pydantic import SecretStr

from domain_kg.models import Namespace
from domain_kg.providers.profiles import (
    ProviderConfig,
    ProviderConnection,
    ProviderProfile,
    ProviderRegistry,
    embedding_space_id,
)


def test_space_identity_tracks_weights_endpoint_and_dimensions_not_credentials_or_extraction():
    profile = ProviderProfile(
        id=uuid4(),
        connection_ref="chat",
        embedding_connection_ref="vectors",
        extraction_model="small-llm",
        embedding_model="embedding-model",
        embedding_dimensions=3,
        data_policy_approved=True,
        allowed_namespaces=(),
    )
    connection = ProviderConnection(
        ref="vectors", endpoint="https://embed.example/v1", api_key=SecretStr("one")
    )
    original = embedding_space_id(profile, connection)
    assert (
        embedding_space_id(
            profile.model_copy(update={"revision": 2, "extraction_model": "new-llm"}), connection
        )
        == original
    )
    assert (
        embedding_space_id(profile, connection.model_copy(update={"api_key": SecretStr("rotated")}))
        == original
    )
    for change in (
        {"embedding_model": "different"},
        {"embedding_dimensions": 4},
        {"embedding_revision": "new-weights"},
    ):
        assert embedding_space_id(profile.model_copy(update=change), connection) != original
    assert (
        embedding_space_id(
            profile, connection.model_copy(update={"endpoint": "https://other.example/v1"})
        )
        != original
    )


def test_separate_approved_embedding_connection_keeps_local_llm_off_embedding_requests():
    namespace = Namespace(org_id=uuid4(), kg_id=uuid4())
    profile = ProviderProfile(
        id=uuid4(),
        connection_ref="chat",
        embedding_connection_ref="vectors",
        extraction_model="small-llm",
        embedding_model="embedding-model",
        embedding_dimensions=3,
        data_policy_approved=True,
        allowed_namespaces=(namespace,),
    )
    chat = ProviderConnection(
        ref="chat", endpoint="http://127.0.0.1:29500/v1", api_key=SecretStr("local")
    )
    vectors = ProviderConnection(
        ref="vectors", endpoint="https://embed.example/v1", api_key=SecretStr("private")
    )
    registry = ProviderRegistry(ProviderConfig(connections=(chat, vectors), profiles=(profile,)))
    assert registry.resolve(profile.id, namespace, restricted=False)[1] == chat
    assert registry.resolve_embedding(profile.id, namespace, restricted=False)[1] == vectors
