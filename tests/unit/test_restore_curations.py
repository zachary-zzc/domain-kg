"""Human edit history changes typed targets, never authors or literal descriptions."""

from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5

import pytest
from test_restore_remapping import example, remapper

from domain_kg.errors import DomainError
from domain_kg.graph.schema import schema_for
from domain_kg.operations.restore_documents import RestoreDocuments
from domain_kg.revisions.curation_publication import directive
from domain_kg.revisions.projections import digest


def history(kind="node"):
    values = example()
    source, _, version, _, fact, curation = values
    target = fact.subject.id if kind == "node" else fact.id
    values_scope = {"public": False, "material_ids": [str(version.material_id)]}
    if kind == "relation":
        values_scope["kind"] = "relation"
    scope = digest(values_scope)
    row = source.model_dump(mode="json") | dict(
        id=str(curation.id),
        kind=kind,
        target_id=str(target),
        target_state_id=str(uuid5(target, f"curation-scope/{scope}")),
        public=False,
        material_ids=[str(version.material_id)],
        source_versions={str(version.material_id): str(version.version_id)},
        scope_key=scope,
        expected_revision=0,
        state="accepted",
        base_fact=None if kind == "node" else fact.model_dump(mode="json"),
        before={},
        after={"description": f"literal {source.kg_id}"},
        rollback_of=None,
        provenance={"actor": "member:historical", "reason": f"reason {source.kg_id}"},
        actor={
            "issuer": "https://oidc.example",
            "subject": "historical",
            "scopes": [],
            "kind": "human",
        },
        job_id=str(uuid4()),
        domain_schema=schema_for(source, "general").model_dump(mode="json"),
        created_at=datetime.now(UTC),
    )
    return values, row


@pytest.mark.parametrize("kind", ["node", "relation"])
def test_history_target_state_and_directive_rebind_without_replaying_human_intent(kind):
    from domain_kg.operations.restore_curations import RestoreCurations

    values, row = history(kind)
    mapping = remapper(values)
    result = RestoreCurations(RestoreDocuments(mapping, originals=())).revision(row)
    assert result["kg_id"] == str(mapping.target.kg_id)
    assert result["after"] == row["after"] and result["actor"] == row["actor"]
    assert result["provenance"] == row["provenance"]
    assert result["target_id"] != row["target_id"]
    assert result["target_state_id"] == str(
        uuid5(UUID(result["target_id"]), f"curation-scope/{row['scope_key']}")
    )
    old, new = directive(row), directive(result)
    expected = mapping.manual_node(old) if kind == "node" else mapping.manual_relation(old)
    assert new == expected
    assert result["source_versions"] == row["source_versions"]


def test_history_rejects_unknown_entity_and_cross_scope_source():
    from domain_kg.operations.restore_curations import RestoreCurations

    values, row = history()
    service = RestoreCurations(RestoreDocuments(remapper(values), originals=()))
    with pytest.raises(DomainError):
        service.revision(row | {"target_id": str(uuid4())})
    row["source_versions"][str(values[2].material_id)] = str(uuid4())
    with pytest.raises(DomainError):
        service.revision(row)
