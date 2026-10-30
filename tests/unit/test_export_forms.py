"""Browser exports require explicit scope and an explicit unverified-content opt-in."""

from uuid import uuid4

import pytest

from domain_kg.errors import DomainError


def test_export_form_keeps_material_scope_and_default_trust():
    from domain_kg.console.export_forms import export_command

    kg, material = uuid4(), uuid4()
    fields = {
        "expected_published_revision": "2",
        "format": "csv",
        "material_ids": str(material),
        "idempotency_key": "browser-export",
    }
    command = export_command(kg, fields)
    assert command.kg_id == kg and command.filters.material_ids == (material,)
    assert command.filters.support_statuses == ("verified", "model_interpreted", "manual")
    assert export_command(kg, fields | {"include_unverified": "on"}).filters.support_statuses == (
        "verified",
        "model_interpreted",
        "manual",
        "imported_unverified",
    )


@pytest.mark.parametrize(
    "changed",
    [
        {"actor": "owner"},
        {"format": "html"},
        {"include_unverified": "silently"},
        {"material_ids": "bad-id"},
    ],
)
def test_invalid_export_form_is_rejected(changed):
    from domain_kg.console.export_forms import export_command

    fields = {
        "expected_published_revision": "1",
        "format": "jsonl",
        "material_ids": "",
        "idempotency_key": "browser-export",
    }
    with pytest.raises(DomainError):
        export_command(uuid4(), fields | changed)
