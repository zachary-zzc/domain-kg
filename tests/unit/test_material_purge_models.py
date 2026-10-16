"""Destructive maintenance commands require a bounded, explicit source target."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_kg.materials.purge_models import PurgeMaterial, PurgeReceipt


def test_material_purge_requires_revision_reason_and_stable_receipt():
    command = PurgeMaterial(
        kg_id=uuid4(),
        material_id=uuid4(),
        expected_revision=2,
        reason="Retention expired",
        idempotency_key="purge-fixture",
    )
    assert not command.dry_run
    with pytest.raises(ValidationError):
        PurgeMaterial.model_validate(command.model_dump() | {"reason": " "})
    with pytest.raises(ValidationError):
        PurgeMaterial.model_validate(command.model_dump() | {"expected_revision": -1})
    with pytest.raises(ValidationError):
        PurgeReceipt(
            org_id=uuid4(),
            kg_id=command.kg_id,
            material_id=command.material_id,
            revision=2,
            state="pending",
        )
