"""Restore suppression as a typed fence, retaining provenance and revision history."""

from uuid import uuid4

import pytest
from test_restore_curations import history
from test_restore_remapping import remapper

from domain_kg.errors import DomainError
from domain_kg.graph.visibility_models import CurationReceipt
from domain_kg.operations.restore_documents import RestoreDocuments


def visibility_history():
    values, row = history()
    row.update(
        kind="visibility",
        before={"suppressed": False},
        after={"suppressed": True},
        identity_groups=[],
        job_id=None,
        visibility_revision=1,
        receipt=CurationReceipt(
            **values[0].model_dump(),
            revision_id=row["id"],
            target_id=row["target_id"],
            action="suppress",
            target_revision=1,
            visibility_revision=1,
        ).model_dump(mode="json"),
    )
    row.pop("domain_schema")
    row.pop("base_fact")
    fence = values[0].model_dump(mode="json") | dict(
        id=row["target_state_id"],
        entity_id=row["target_id"],
        curation=values[-1].model_dump(mode="json"),
        scope_key=row["scope_key"],
        revision=1,
        public=False,
        material_ids=row["material_ids"],
        suppressed=True,
        identity_groups=[],
    )
    return values, row, fence


def test_visibility_history_and_current_fence_rebind_typed_references():
    from domain_kg.operations.restore_visibility import RestoreVisibility

    values, row, fence = visibility_history()
    mapping = remapper(values)
    service = RestoreVisibility(RestoreDocuments(mapping, originals=()))
    restored = service.revision(row)
    current = service.fence(fence, restored)
    assert restored["target_id"] != row["target_id"]
    assert restored["actor"] == row["actor"] and restored["provenance"] == row["provenance"]
    assert restored["source_versions"] == row["source_versions"]
    assert restored["after"] == {"suppressed": True}
    receipt = CurationReceipt.model_validate(restored["receipt"])
    assert receipt.kg_id == mapping.target.kg_id and str(receipt.target_id) == restored["target_id"]
    assert current["id"] == restored["target_state_id"]
    assert current["curation"]["kg_id"] == str(mapping.target.kg_id) and current["suppressed"]


@pytest.mark.parametrize("corruption", ["entity", "receipt", "fence"])
def test_visibility_history_rejects_unknown_targets_and_forged_backlinks(corruption):
    from domain_kg.operations.restore_visibility import RestoreVisibility

    values, row, fence = visibility_history()
    service = RestoreVisibility(RestoreDocuments(remapper(values), originals=()))
    if corruption == "entity":
        row["target_id"] = str(uuid4())
    elif corruption == "receipt":
        row["receipt"]["target_id"] = str(uuid4())
    else:
        fence["curation"]["id"] = str(uuid4())
    with pytest.raises(DomainError):
        restored = service.revision(row)
        service.fence(fence, restored)
