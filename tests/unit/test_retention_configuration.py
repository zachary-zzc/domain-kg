"""Retention policy is bounded private server configuration, never request input."""

import pytest

from domain_kg.errors import DomainError
from domain_kg.operations.retention_policy import RetentionPolicy
from domain_kg.settings import Settings


def test_retention_policy_configuration_is_explicit_and_bounded(tmp_path):
    assert RetentionPolicy.from_file(None) == RetentionPolicy()
    config = tmp_path / "retention.json"
    config.write_text('{"grace_seconds":7200,"max_targets":2}')
    policy = RetentionPolicy.from_file(config)
    assert policy.grace_seconds == 7200 and policy.max_targets == 2
    assert Settings(retention_config=config).retention_config == config
    for content in ('{"grace_seconds":0}', '{"path":"arbitrary"}', "x" * 65537):
        config.write_text(content)
        with pytest.raises(DomainError) as error:
            RetentionPolicy.from_file(config)
        assert str(config) not in error.value.message
    with pytest.raises(DomainError):
        RetentionPolicy.from_file(tmp_path / "missing")


def test_retention_configuration_bounds_preflight_objects_and_bytes():
    from pydantic import ValidationError

    policy = RetentionPolicy(max_objects_per_target=1000, max_verify_bytes=16 * 1024**2)
    assert policy.max_objects_per_target == 1000 and policy.max_verify_bytes == 16 * 1024**2
    for values in ({"max_objects_per_target": 0}, {"max_verify_bytes": 0}):
        with pytest.raises(ValidationError):
            RetentionPolicy.model_validate(values)
