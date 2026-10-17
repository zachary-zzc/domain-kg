"""Cleanup selects a recorded restore job, never an arbitrary target namespace or key."""

from uuid import uuid4

import pytest
from pydantic import ValidationError


def test_restore_cleanup_command_has_only_owner_intent_and_recorded_restore_job():
    from domain_kg.jobs.models import JobCommand
    from domain_kg.operations.models import RestoreCleanupCommand

    command = RestoreCleanupCommand(
        kg_id=uuid4(),
        restore_job_id=uuid4(),
        expected_published_revision=0,
        idempotency_key="discard-inactive-restore",
    )
    assert (
        JobCommand(
            org_id=uuid4(),
            kg_id=command.kg_id,
            id=uuid4(),
            kind="restore_cleanup",
            target_id=uuid4(),
            idempotency_key=command.idempotency_key,
        ).kind
        == "restore_cleanup"
    )
    for extra in (
        {"target_kg_id": str(uuid4())},
        {"bucket": "other"},
        {"path": "arbitrary"},
        {"actor": "owner"},
    ):
        with pytest.raises(ValidationError):
            RestoreCleanupCommand.model_validate(command.model_dump(mode="json") | extra)
