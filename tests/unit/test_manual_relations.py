"""Manual semantic identities survive metadata edits and cannot fabricate spans."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_kg.graph.manual_relations import ManualRelation
from domain_kg.graph.models import AssertionRecord
from domain_kg.models import ScopedRef
from domain_kg.revisions.curation_publication import directive


def relation_record():
    org, kg, identifier, logical, subject, target, material = (uuid4() for _ in range(7))
    ns = {"org_id": str(org), "kg_id": str(kg)}
    base = AssertionRecord(
        **ns,
        id=logical,
        subject=ScopedRef(**ns, id=subject),
        target=ScopedRef(**ns, id=target),
        predicate="uses",
    )
    return ns | {
        "id": str(identifier),
        "kind": "relation",
        "target_id": str(logical),
        "after": {"predicate": "related_to"},
        "expected_revision": 0,
        "created_at": datetime.now(UTC),
        "public": False,
        "material_ids": [str(material)],
        "provenance": {"actor": "fixture-editor", "reason": "Reviewed relationship"},
        "base_fact": base.model_dump(mode="json"),
    }


def test_metadata_revision_keeps_manual_assertion_semantic_identity():
    record = relation_record()
    first = directive(record)
    later = directive(
        record
        | {
            "id": str(uuid4()),
            "after": record["after"] | {"tags": ["reviewed"]},
            "expected_revision": 1,
        }
    )
    assert isinstance(first, ManualRelation) and isinstance(later, ManualRelation)
    assert first.fact.id == later.fact.id
    assert first.support.id != later.support.id
    assert first.support.version is None and first.support.spans == ()
    assert first.text == later.text


def test_manual_claim_rejects_foreign_namespace_and_fabricated_document_ownership():
    manual = directive(relation_record())
    assert isinstance(manual, ManualRelation)
    body = manual.model_dump(mode="json")
    body["support"]["assertion"]["kg_id"] = str(uuid4())
    with pytest.raises(ValidationError):
        ManualRelation.model_validate(body)
    body = manual.model_dump(mode="json")
    body["support"]["version"] = body["curation"] | {"id": str(uuid4())}
    with pytest.raises(ValidationError):
        ManualRelation.model_validate(body)
