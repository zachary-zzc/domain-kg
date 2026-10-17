"""Counter relationships cannot claim a precise unbounded backlog."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_kg.operations.work_metrics import ProjectionWork


def test_work_sample_invariants_and_age():
    now = datetime.now(UTC)
    base = dict(
        org_id=uuid4(),
        kg_id=uuid4(),
        as_of=now,
        sampled_jobs=0,
        pending_jobs=0,
        failed_jobs=0,
        repair_required_jobs=0,
        truncated=False,
        oldest_pending_at=None,
        oldest_pending_seconds=None,
    )
    assert ProjectionWork(**base).oldest_pending_at is None
    valid = base | dict(
        sampled_jobs=1,
        pending_jobs=1,
        oldest_pending_at=now - timedelta(seconds=15),
        oldest_pending_seconds=15,
    )
    assert ProjectionWork(**valid).oldest_pending_seconds == 15
    for mutation in (
        {"sampled_jobs": 2},
        {"truncated": True},
        {"oldest_pending_seconds": 14},
        {"oldest_pending_at": None},
        {"oldest_pending_seconds": -1},
    ):
        with pytest.raises(ValidationError):
            ProjectionWork(**(valid | mutation))


def test_naive_bson_dates_are_utc():
    from domain_kg.operations.work_metrics import _WorkRow

    timestamp = datetime(2026, 10, 18, 0, 0)
    row = _WorkRow(
        org_id=uuid4(),
        kg_id=uuid4(),
        id=uuid4(),
        kind="ingest",
        stage="queued",
        created_at=timestamp,
    )
    assert row.created_at == timestamp.replace(tzinfo=UTC)
