"""Expired-data cleanup is explicit owner intent without storage paths or identity arguments."""

from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError


def test_incomplete_backup_collection_is_an_explicit_resource_selection():
    from domain_kg.operations.models import RetentionCommand

    command = RetentionCommand(
        kg_id=uuid4(),
        expected_published_revision=0,
        resources="incomplete_backups",
        idempotency_key="collect-failed-backup",
    )
    assert command.resources == "incomplete_backups"


def test_retention_command_requires_an_explicit_resource_selection():
    from domain_kg.jobs.models import JobCommand
    from domain_kg.operations.models import OwnerCommand, RetentionCommand

    fields = dict(kg_id=uuid4(), expected_published_revision=0, idempotency_key="cleanup-expired")
    with pytest.raises(ValidationError):
        RetentionCommand.model_validate(fields)
    command = RetentionCommand(**fields, resources="exports")
    assert TypeAdapter(OwnerCommand).validate_python(command) == command
    job = JobCommand(
        org_id=uuid4(),
        kg_id=command.kg_id,
        id=uuid4(),
        kind="retention",
        target_id=uuid4(),
        idempotency_key=command.idempotency_key,
    )
    assert job.kind == "retention" and job.restore_intent_id is None
    for extra in (
        {"bucket": "other"},
        {"path": "/source"},
        {"actor": "owner"},
        {"resources": "delete_all"},
    ):
        with pytest.raises(ValidationError):
            RetentionCommand.model_validate(command.model_dump() | extra)
