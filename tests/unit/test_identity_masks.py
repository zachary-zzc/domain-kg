"""A private alias must not turn a public fence into a global alias fence."""

from test_identity_graph_views import graph_fixture

from domain_kg.graph.visibility import NodeMasks
from domain_kg.graph.visibility_models import NodeFence
from domain_kg.models import Namespace


def test_fence_expands_in_each_source_without_hiding_an_independent_source_alias():
    data, alpha, beta, _, a, b, identity = graph_fixture()
    fence = NodeFence(
        org_id=identity.curation.org_id,
        kg_id=identity.curation.kg_id,
        entity_id=alpha,
        curation=identity.curation,
        revision=1,
        public=True,
    )
    masks = NodeMasks(Namespace(**fence.model_dump(include={"org_id", "kg_id"})), (fence,))
    expanded = masks.expanded(data.identities, (a, b))
    assert expanded.blocked(alpha) and expanded.blocked(beta, material_id=a)
    assert not expanded.blocked(beta, material_id=b)
    assert not expanded.blocked(beta)
    # Persisted original members remain fenced after the mapping is withdrawn or split.
    retained = expanded.expanded(None, (a, b))
    assert retained.blocked(beta, material_id=a)


def test_public_field_does_not_follow_a_suppressed_private_identity_context():
    from uuid import uuid4

    from domain_kg.graph.curation_models import ManualNode, NodeFields
    from domain_kg.graph.repository import GraphData, GraphRepository

    data, alpha, beta, _, a, b, identity = graph_fixture()
    fence = NodeFence(
        org_id=identity.curation.org_id,
        kg_id=identity.curation.kg_id,
        entity_id=alpha,
        curation=identity.curation,
        revision=1,
        material_ids=(a,),
    )
    manual = ManualNode(
        curation=identity.curation.model_copy(update={"id": uuid4()}),
        entity=identity.target.model_copy(update={"id": beta}),
        public=True,
        changes=NodeFields(name="Public Beta only"),
        provenance=identity.provenance,
        revision=1,
        created_at=identity.created_at,
    )
    masked = GraphData(
        data.scope.model_copy(update={"node_fences": (fence,)}),
        tuple(row for row in data.rows if row.source.material_id == b),
        (*data.curations, manual),
    )
    node = GraphRepository(None, None).node(masked, alpha)
    assert node.name == "Alpha" and manual not in node.curations
    assert GraphRepository(None, None).node(masked, beta).name == "Public Beta only"


def test_public_singleton_fence_does_not_allocate_one_snapshot_per_visible_source():
    from uuid import uuid4

    data, alpha, _, _, _, _, identity = graph_fixture()
    fence = NodeFence(
        org_id=identity.curation.org_id,
        kg_id=identity.curation.kg_id,
        entity_id=alpha,
        curation=identity.curation,
        revision=1,
        public=True,
    )
    masks = NodeMasks(Namespace(**fence.model_dump(include={"org_id", "kg_id"})), (fence,))
    expanded = masks.expanded(None, tuple(uuid4() for _ in range(102)))
    assert expanded.blocked(alpha) and expanded.fences[0].identity_groups == ()


def test_public_group_snapshot_is_not_duplicated_for_each_source():
    from uuid import uuid4

    from domain_kg.graph.identity_models import ManualIdentity
    from domain_kg.graph.identity_partition import IdentityPartition

    _, alpha, beta, _, _, _, identity = graph_fixture()
    public = ManualIdentity.model_validate(
        identity.model_dump() | {"public": True, "material_ids": ()}
    )
    namespace = Namespace(org_id=identity.curation.org_id, kg_id=identity.curation.kg_id)
    fence = NodeFence(
        **namespace.model_dump(),
        entity_id=alpha,
        curation=identity.curation,
        revision=1,
        public=True,
    )
    materials = tuple(uuid4() for _ in range(102))
    expanded = NodeMasks(namespace, (fence,)).expanded(
        IdentityPartition(namespace, (public,)), materials
    )
    assert len(expanded.fences[0].identity_groups) == 1
    assert expanded.fences[0].identity_groups[0].material_id is None
    assert all(expanded.blocked(beta, material_id=material) for material in materials)
