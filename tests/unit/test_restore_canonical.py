"""Canonical restore records preserve provenance and reject unscoped references."""

from uuid import uuid4

import pytest
from test_restore_remapping import example, remapper

from domain_kg.errors import DomainError
from domain_kg.graph.models import EntityRecord, SupportRecord
from domain_kg.models import ScopedRef
from domain_kg.storage.models import CurationRecord, MaterialRecord, MaterialVersionRecord


def records():
    values = example()
    source, target, version, observations, fact, curation = values
    mapping = remapper(values)
    rows = {
        "materials": [
            MaterialRecord(
                **source.model_dump(), id=version.material_id, title=f"Literal {source.kg_id}"
            ).model_dump(mode="json")
        ],
        "material_versions": [
            MaterialVersionRecord(
                **source.model_dump(),
                id=version.version_id,
                material=ScopedRef(**source.model_dump(), id=version.material_id),
            ).model_dump(mode="json")
        ],
        "entities": [
            EntityRecord(**o.entity.model_dump()).model_dump(mode="json") for o in observations
        ],
        "assertions": [fact.model_dump(mode="json")],
        "curations": [
            CurationRecord(
                **curation.model_dump(), actor="historical", reason=f"Reason {source.kg_id}"
            ).model_dump(mode="json")
        ],
        "supports": [
            SupportRecord(
                **curation.model_dump(),
                assertion=ScopedRef(**source.model_dump(), id=fact.id),
                status="manual",
                curation=curation,
                provenance={"actor": "historical", "reason": f"Reason {source.kg_id}"},
            ).model_dump(mode="json")
        ],
    }
    return mapping, rows, version, fact


def test_canonical_records_rebind_references_in_dependency_order_and_preserve_user_text():
    from domain_kg.operations.restore_canonical import RestoreCanonical

    mapping, rows, version, fact = records()
    result = RestoreCanonical(mapping).records(rows)
    assert isinstance(result[0], MaterialRecord) and isinstance(result[1], MaterialVersionRecord)
    assert all(record.kg_id == mapping.target.kg_id for record in result)
    material = next(record for record in result if isinstance(record, MaterialRecord))
    assert material.title == rows["materials"][0]["title"]
    support = next(record for record in result if isinstance(record, SupportRecord))
    assert support.status == "manual" and support.version is None
    assert support.provenance.actor == "historical"
    assert support.assertion.id == mapping.assertion(fact).id
    assert support.curation.kg_id == mapping.target.kg_id


def test_canonical_transform_fails_closed_on_foreign_or_unknown_references():
    from domain_kg.operations.restore_canonical import RestoreCanonical

    mapping, rows, _, _ = records()
    rows["entities"][0]["kg_id"] = str(uuid4())
    with pytest.raises(DomainError):
        RestoreCanonical(mapping).records(rows)
    mapping, rows, _, _ = records()
    rows["supports"][0]["assertion"]["id"] = str(uuid4())
    with pytest.raises(DomainError):
        RestoreCanonical(mapping).records(rows)

    mapping, rows, _, _ = records()
    rows["material_versions"][0]["material"]["kg_id"] = str(uuid4())
    with pytest.raises(DomainError):
        RestoreCanonical(mapping).records(rows)
