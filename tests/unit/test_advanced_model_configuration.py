from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_provider_configuration import command, private_file

from domain_kg.errors import DomainError
from domain_kg.models import Namespace
from domain_kg.providers.configuration import ModelConfiguration, ProfileFile
from domain_kg.providers.profiles import ProviderRegistry


def advanced(**changes):
    return ModelConfiguration.model_validate(command().model_dump() | changes)


def test_edit_keeps_a_saved_key_only_for_the_same_endpoint(tmp_path):
    path = private_file(tmp_path)
    namespace = Namespace(org_id=uuid4(), kg_id=uuid4())
    store, original = ProfileFile(path), uuid4()
    store.add(
        original,
        namespace,
        advanced(endpoint="https://api.example/v1", api_key="private-key"),
        dimensions=1024,
    )
    edit = advanced(
        endpoint="https://api.example/v1",
        credential_profile_id=original,
        extraction_model="changed-model",
    )
    prepared = store.prepare(uuid4(), namespace, edit, dimensions=1024)
    assert prepared.chat.api_key.get_secret_value() == "private-key"
    assert prepared.profile.extraction_format == "source_names_v1"
    assert "private-key" not in repr(prepared)
    with pytest.raises(DomainError):
        store.prepare(
            uuid4(),
            namespace,
            edit.model_copy(update={"endpoint": "https://other.example/v1"}),
            dimensions=1024,
        )
    with pytest.raises(DomainError):
        store.prepare(uuid4(), Namespace(org_id=uuid4(), kg_id=uuid4()), edit, dimensions=1024)


def test_claude_and_local_chat_can_reuse_approved_embeddings_without_copying_keys(tmp_path):
    path = private_file(tmp_path)
    namespace = Namespace(org_id=uuid4(), kg_id=uuid4())
    store, original = ProfileFile(path), uuid4()
    store.add(original, namespace, advanced(api_key="private-embedding-key"), dimensions=1024)
    prepared = store.prepare(
        uuid4(),
        namespace,
        advanced(
            endpoint="https://api.anthropic.com/v1",
            api_format="anthropic",
            api_key="private-claude-key",
            embedding_profile_id=original,
        ),
        dimensions=1024,
    )
    assert prepared.chat.api_format == "anthropic"
    assert prepared.embedding.api_format == "openai"
    assert prepared.embedding.api_key.get_secret_value() == "private-embedding-key"
    assert prepared.profile.embedding_connection_ref == "model-" + str(original)
    with pytest.raises(DomainError):
        store.prepare(
            uuid4(),
            Namespace(org_id=uuid4(), kg_id=uuid4()),
            advanced(embedding_profile_id=original),
            dimensions=1024,
        )


def test_the_default_survives_later_saves_and_cannot_grant_another_organization_access(tmp_path):
    path = private_file(tmp_path)
    namespace = Namespace(org_id=uuid4(), kg_id=uuid4())
    store, first = ProfileFile(path), uuid4()
    store.add(first, namespace, command(), dimensions=1024)
    store.set_default(namespace, first)
    store.add(uuid4(), namespace, command(), dimensions=1024)
    registry = ProviderRegistry.from_file(path)
    assert registry.default_profile_id == first
    with pytest.raises(DomainError):
        store.set_default(Namespace(org_id=uuid4(), kg_id=uuid4()), first)


def test_native_claude_does_not_silently_accept_an_unsupported_thinking_override():
    with pytest.raises(ValidationError):
        advanced(
            api_format="anthropic", embedding_endpoint="https://embedding.example/v1",
            thinking="enabled",
        )
