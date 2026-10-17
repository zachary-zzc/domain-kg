"""Resealed legacy admission history remains usable without an old upload capability."""

from uuid import uuid4

import pytest
from test_restore_imports import imported

from domain_kg.errors import DomainError
from domain_kg.imports.admission import record_matches_plan
from domain_kg.operations.restore_documents import RestoreDocuments
from domain_kg.operations.restore_imports import restore_import


def import_record():
    mapping, blob, plan, graph, origin, prepared, body = imported()
    bundle = restore_import(mapping, plan, graph, origin, body)
    documents = RestoreDocuments(mapping, originals=(blob,), imports=(bundle,))
    record = mapping.origin.model_dump(mode="json") | dict(
        id=str(plan.id),
        issuer="https://issuer.example",
        subject="historical",
        key="history",
        intent="a" * 64,
        state="admitted",
        job_id=str(uuid4()),
        source=documents.owner(blob).model_dump(mode="json"),
        plan=plan.model_dump(mode="json"),
        graph=graph.model_dump(mode="json"),
        plan_hash=origin.plan_hash,
        graph_hash=origin.graph_hash,
        binding={
            "schema": prepared.domain_schema.model_dump(mode="json"),
            "blob": blob.model_dump(mode="json"),
            "principal_kind": "human",
        },
    )
    return documents, bundle, record


def test_import_admission_history_uses_resealed_scope_and_exact_original():
    from domain_kg.operations.restore_import_history import RestoreImportHistory

    documents, bundle, record = import_record()
    rewritten = RestoreImportHistory(documents).plan(record)
    assert record_matches_plan(rewritten, bundle.plan)
    assert rewritten["source"]["kg_id"] == str(documents.mapping.target.kg_id)
    assert rewritten["binding"]["blob"]["sha256"] == record["binding"]["blob"]["sha256"]
    assert rewritten["binding"]["schema"]["kg_id"] == str(documents.mapping.target.kg_id)
    assert rewritten["subject"] == "historical" and rewritten["state"] == "admitted"


def test_import_admission_history_rejects_forged_seal_and_wrong_source():
    from domain_kg.operations.restore_import_history import RestoreImportHistory

    documents, _, record = import_record()
    service = RestoreImportHistory(documents)
    with pytest.raises(DomainError):
        service.plan(record | {"graph_hash": "0" * 64})
    with pytest.raises(DomainError):
        service.plan(record | {"source": record["source"] | {"kg_id": str(uuid4())}})


@pytest.mark.parametrize("kind", ["observation", "support"])
def test_import_id_mappings_reject_forged_owners_and_match_future_owned_ids(kind):
    from domain_kg.imports.build import owned_id
    from domain_kg.models import ScopedRef
    from domain_kg.operations.restore_import_history import RestoreImportHistory

    values = imported()
    mapping, blob, plan, graph, origin, _, body = values
    bundle = restore_import(mapping, plan, graph, origin, body)
    documents = RestoreDocuments(mapping, originals=(blob,), imports=(bundle,))
    old = graph.entities[0] if kind == "observation" else graph.relations[0]
    new = next(
        item
        for item in (bundle.graph.entities if kind == "observation" else bundle.graph.relations)
        if (item.source_id, item.legacy_id) == (old.source_id, old.legacy_id)
    )
    identifier = owned_id(origin, kind, old.source_id, old.legacy_id)
    key = "entity" if kind == "observation" else "assertion"
    reference_id = old.entity_id if kind == "observation" else old.assertion_id
    row = documents.owner(blob).model_dump(mode="json") | dict(
        id=str(identifier),
        plan_id=str(plan.id),
        origin=origin.origin,
        legacy_source_id=old.source_id,
        legacy_id=old.legacy_id,
    )
    row[key] = ScopedRef(**mapping.origin.model_dump(), id=reference_id).model_dump(mode="json")
    if kind == "support":
        row["support"] = ScopedRef(**mapping.origin.model_dump(), id=identifier).model_dump(
            mode="json"
        )
    service = RestoreImportHistory(documents)
    result = service.mapping_row(kind, row)
    assert result["id"] == str(owned_id(bundle.origin, kind, old.source_id, old.legacy_id))
    assert result[key]["id"] == str(new.entity_id if kind == "observation" else new.assertion_id)
    with pytest.raises(DomainError):
        service.mapping_row(kind, row | {key: row[key] | {"id": str(uuid4())}})
