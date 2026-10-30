"""Batch boundaries reject ambiguous, excessive or unguarded commands."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_kg.operations.batch import BatchCommand


@pytest.mark.parametrize("operation", ["delete", "restore", "process"])
def test_material_batch_requires_expected_revision(operation):
    with pytest.raises(ValidationError):
        BatchCommand(operation=operation, items=({"id": uuid4()},), idempotency_key="guard")


@pytest.mark.parametrize(
    "items", [[], [{"id": uuid4()}] * 2, [{"id": uuid4()} for _ in range(101)]]
)
def test_job_batch_rejects_empty_duplicate_and_excessive_targets(items):
    with pytest.raises(ValidationError):
        BatchCommand(operation="retry", items=items, idempotency_key="bounded")


def test_batch_rejects_actor_fields_and_source_versions_on_job_controls():
    for changes in ({"actor": "owner"}, {"items": ({"id": uuid4(), "version_id": uuid4()},)}):
        with pytest.raises(ValidationError):
            BatchCommand.model_validate(
                {"operation": "cancel", "items": [{"id": uuid4()}], "idempotency_key": "strict"}
                | changes
            )
