"""Graph UI data cannot widen the service view or discard its source qualification."""

from uuid import uuid4

import pytest

from domain_kg.console.graph_data import graph_data
from domain_kg.errors import DomainError
from domain_kg.graph.views import EdgeView, NodeView, SubgraphView


def node(kg, identifier=None, name="Visible node"):
    return NodeView(
        kg_id=kg,
        id=identifier or uuid4(),
        name=name,
        aliases=(),
        descriptions=(),
        entity_types=("entity",),
        observations=(),
        observation_count=1,
        support_count=1,
        neighbor_count=1,
    )


def edge(left, right, status="verified"):
    return EdgeView(
        subject_id=left.id,
        target_id=right.id,
        assertion_id=uuid4(),
        support_id=uuid4(),
        predicate="includes",
        polarity="positive",
        confidence=None,
        source=None,
        spans=(),
        status=status,
    )


def test_scoped_graph_links_and_independent_evidence_statuses():
    kg, material, version = uuid4(), uuid4(), uuid4()
    left, right = node(kg, name="<img src=x onerror=bad>"), node(kg)
    edges = tuple(
        edge(left, right, status) for status in ("verified", "manual", "imported_unverified")
    )
    view = SubgraphView(kg_id=kg, revision=1, nodes=(left, right), edges=edges, truncation=True)
    result = graph_data(view, kg, include_unverified=True, material_id=material, version_id=version)
    assert result["nodes"][0]["name"] == left.name
    assert (
        str(material) in result["nodes"][0]["href"] and str(version) in result["nodes"][0]["href"]
    )
    assert "include_unverified=true" in result["nodes"][0]["href"]
    assert len({item["id"] for item in result["edges"]}) == 3
    assert {item["status"] for item in result["edges"]} == {
        "verified",
        "manual",
        "imported_unverified",
    }
    assert all(str(kg) in item["href"] for item in result["edges"])
    assert result["truncated"] is True
    assert "observations" not in result["nodes"][0]


def test_graph_rejects_wrong_namespace_dangling_duplicates_and_oversize():
    kg = uuid4()
    left, right = node(kg), node(kg)
    for view in (
        SubgraphView(kg_id=uuid4(), revision=1, nodes=(left,), edges=()),
        SubgraphView(kg_id=kg, revision=1, nodes=(left, node(uuid4())), edges=()),
        SubgraphView(kg_id=kg, revision=1, nodes=(left,), edges=(edge(left, right),)),
        SubgraphView(kg_id=kg, revision=1, nodes=(left, left), edges=()),
        SubgraphView(kg_id=kg, revision=1, nodes=tuple(node(kg) for _ in range(26)), edges=()),
        SubgraphView(
            kg_id=kg,
            revision=1,
            nodes=(left, right),
            edges=tuple(edge(left, right) for _ in range(41)),
        ),
    ):
        with pytest.raises(DomainError):
            graph_data(view, kg)


def test_graph_json_and_fallback_names_are_escaped():
    import json
    from html.parser import HTMLParser

    from domain_kg.console.rendering import TEMPLATES

    kg = uuid4()
    value = node(kg, name="</script><img src=x onerror=bad> & 'quoted'")
    payload = graph_data(SubgraphView(kg_id=kg, revision=1, nodes=(value,), edges=()), kg)
    rendered = TEMPLATES.get_template("graph.html").render(graph_data=payload)
    assert "<img src=x" not in rendered

    class Data(HTMLParser):
        def handle_starttag(self, tag, attrs):
            for key, val in attrs:
                if key == "data-graph":
                    self.value = json.loads(val)

    parser = Data()
    parser.feed(rendered)
    assert parser.value["nodes"][0]["name"] == value.name


def test_source_relationship_opens_its_exact_original_version():
    from domain_kg.models import SourceVersion

    org, kg, material, version = uuid4(), uuid4(), uuid4(), uuid4()
    left, right = node(kg), node(kg)
    fact = edge(left, right).model_copy(
        update={
            "source": SourceVersion(org_id=org, kg_id=kg, material_id=material, version_id=version)
        }
    )
    view = SubgraphView(kg_id=kg, revision=1, nodes=(left, right), edges=(fact,))
    href = graph_data(view, kg)["edges"][0]["href"]
    assert f"/materials/{material}?" in href
    assert f"version_id={version}" in href


def test_manual_relationship_keeps_human_history_as_its_primary_evidence():
    from domain_kg.models import SourceVersion

    org, kg = uuid4(), uuid4()
    left, right = node(kg), node(kg)
    fact = edge(left, right, "manual").model_copy(
        update={
            "source": SourceVersion(org_id=org, kg_id=kg, material_id=uuid4(), version_id=uuid4())
        }
    )
    view = SubgraphView(kg_id=kg, revision=1, nodes=(left, right), edges=(fact,))
    assert "/relations/" in graph_data(view, kg)["edges"][0]["href"]
    with pytest.raises(DomainError):
        graph_data(view, kg, org_id=uuid4())
