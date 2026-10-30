import pytest

from domain_kg.errors import DomainError
from domain_kg.models import Namespace
from domain_kg.providers.profiles import ProviderConfig, ProviderRegistry
from scripts.configure_alibaba import EXAMPLE, configuration, credential


def test_example_is_disabled_and_private_config_scopes_public_data(tmp_path):
    from uuid import uuid4

    ns = Namespace(org_id=uuid4(), kg_id=uuid4())
    registry = ProviderRegistry(ProviderConfig.model_validate_json(EXAMPLE.read_bytes()))
    profile = next(iter(registry.profiles.values()))
    with pytest.raises(DomainError):
        registry.resolve(profile.id, ns, restricted=False)
    config = configuration(ns, "fixture-key")
    registry = ProviderRegistry(config)
    approved, _ = registry.resolve(profile.id, ns, restricted=False)
    assert approved.embedding_dimensions == 1024 and approved.embedding_batch_size == 10
    assert approved.extraction_response_format == "json_object"
    assert approved.extraction_format == "source_names_v1"
    assert approved.max_completion_tokens == 8192
    with pytest.raises(DomainError):
        registry.resolve(profile.id, ns, restricted=True)
    with pytest.raises(DomainError):
        registry.resolve(profile.id, Namespace(org_id=ns.org_id, kg_id=uuid4()), restricted=False)
    with pytest.raises(ValueError):
        configuration(ns, "fixture-key", endpoint="https://attacker.example/compatible-mode/v1")
    secret = tmp_path / "key"
    secret.write_text("fixture-key\n")
    secret.chmod(0o600)
    assert credential(secret) == "fixture-key"
    secret.chmod(0o644)
    with pytest.raises(ValueError):
        credential(secret)
