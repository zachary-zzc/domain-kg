"""Type filters use only eligible human fields of the claim's own source scope."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

from test_manual_relations import relation_record

from domain_kg.graph.curation_models import ManualNode, NodeFields
from domain_kg.graph.repository import GraphData
from domain_kg.models import ManualProvenance, ScopedRef
from domain_kg.retrieval.models import SearchFilters
from domain_kg.revisions.curation_publication import directive
from domain_kg.revisions.projections import ProjectionBody


def manual_fixture(*, own_type=True, wanted_type="concept"):
    record = relation_record()
    relation = directive(record)
    source = UUID(record["material_ids"][0])
    node = ManualNode(
        curation=ScopedRef(
            org_id=relation.curation.org_id, kg_id=relation.curation.kg_id, id=uuid4()
        ),
        entity=relation.fact.subject,
        changes=NodeFields(name="Human label", entity_type="concept"),
        provenance=ManualProvenance(actor="fixture-editor", reason="Explicit human type"),
        revision=1,
        created_at=datetime.now(UTC),
        material_ids=(source if own_type else uuid4(),),
    )
    row = ProjectionBody(
        org_id=relation.curation.org_id,
        kg_id=relation.curation.kg_id,
        id=relation.curation.id,
        curation=relation.curation,
        kind="curation",
        manual_text=relation.text,
        provenance=relation.provenance,
        manual_relation=relation,
    )
    scope = SimpleNamespace(filters=SearchFilters(entity_types=(wanted_type,)))
    return GraphData(scope, (row,), (node, relation)), node


def test_retained_manual_relationship_can_use_its_explicit_human_endpoint_type():
    data, node = manual_fixture()
    assert len(data.supports) == 1
    assert node in data.manual_nodes


def test_human_type_from_an_independent_scope_cannot_qualify_a_claim():
    data, _ = manual_fixture(own_type=False)
    assert data.supports == ()


def test_manual_node_does_not_bypass_an_unmatched_type_filter():
    data, _ = manual_fixture(wanted_type="unrelated")
    assert data.manual_nodes == ()
