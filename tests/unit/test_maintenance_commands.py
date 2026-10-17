"""Maintenance targets an accepted graph owner, never a fabricated source version."""

from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError


def test_maintenance_command_is_typed_owner_work_without_transport_or_identity_arguments():
    from domain_kg.operations.models import MaintenanceCommand, OwnerCommand

    command = MaintenanceCommand(
        kg_id=uuid4(),
        expected_published_revision=2,
        curation_id=uuid4(),
        idempotency_key="repair-human-projection",
    )
    assert command.kind == "maintenance" and command.mode == "repair_curation"
    assert TypeAdapter(OwnerCommand).validate_python(command) == command
    for field, value in (("actor", "owner"), ("database", "private"), ("path", "/tmp/source")):
        with pytest.raises(ValidationError):
            MaintenanceCommand.model_validate(command.model_dump() | {field: value})


def test_maintenance_command_rejects_undeclared_modes_and_empty_revision():
    from domain_kg.operations.models import MaintenanceCommand

    command = dict(
        kg_id=uuid4(),
        expected_published_revision=2,
        curation_id=uuid4(),
        idempotency_key="typed-repair",
    )
    with pytest.raises(ValidationError):
        MaintenanceCommand.model_validate(command | {"mode": "run_database_command"})
    with pytest.raises(ValidationError):
        MaintenanceCommand.model_validate(command | {"expected_published_revision": -1})
