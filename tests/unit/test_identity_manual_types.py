"""Retained human types follow the claim's own identity context."""

from types import SimpleNamespace
from uuid import uuid4

from test_manual_type_filters import manual_fixture

from domain_kg.graph.identity_models import IdentityGroup, ManualIdentity
from domain_kg.graph.repository import GraphData


def test_opaque_manual_claim_uses_human_type_on_a_grouped_original():
    data, node = manual_fixture()
    alpha, beta = uuid4(), node.entity.id
    manual = node.model_copy(update={"entity": node.entity.model_copy(update={"id": alpha})})
    identity = ManualIdentity(
        curation=node.curation.model_copy(update={"id": uuid4()}),
        target=manual.entity,
        groups=(IdentityGroup(canonical_id=alpha, member_ids=(alpha, beta)),),
        provenance=node.provenance,
        revision=1,
        created_at=node.created_at,
        material_ids=node.material_ids,
    )
    scope = SimpleNamespace(
        filters=data.scope.filters,
        view=data.scope.view,
        node_fences=(),
        visible_sources=(SimpleNamespace(material_id=node.material_ids[0]),),
        visible_curations=(identity.curation,),
    )
    grouped = GraphData(scope, data.rows, (manual, data.curations[1], identity))
    assert len(grouped.supports) == 1
    assert manual in grouped.manual_nodes
