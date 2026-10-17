"""Logical expiry and the physical cleanup grace are separate, bounded settings."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError


def test_retention_grace_blocks_live_and_recently_expired_objects():
    from domain_kg.operations.retention_policy import RetentionPolicy

    policy = RetentionPolicy(grace_seconds=3600)
    now = datetime(2026, 10, 17, 12, tzinfo=UTC)
    assert not policy.eligible(now + timedelta(days=1), at=now)
    assert not policy.eligible(now - timedelta(minutes=59), at=now)
    assert policy.eligible(now - timedelta(hours=1), at=now)
    assert policy.eligible(now - timedelta(days=7), at=now)
    with pytest.raises(ValidationError):
        policy.eligible(now.replace(tzinfo=None), at=now)
    with pytest.raises(ValidationError):
        RetentionPolicy(grace_seconds=0)


def test_retention_example_has_bounded_serial_cleanup_and_no_auto_run():
    from domain_kg.operations.retention_policy import RetentionPolicy

    path = Path(__file__).resolve().parents[2] / "config/retention.example.json"
    policy = RetentionPolicy.model_validate_json(path.read_bytes())
    assert policy.grace_seconds == 86400 and policy.max_targets == 10
    assert policy.erasure.page_size == 100 and policy.erasure.max_records == 1000
    assert policy.erasure.max_pages == 20
    assert "auto" not in policy.model_dump_json()
