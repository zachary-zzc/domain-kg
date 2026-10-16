"""Untrusted legacy graphs are bounded data; namespace and source ownership stay explicit."""

import json
from uuid import uuid4

import pytest

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.imports.legacy import parse_legacy
from domain_kg.models import Namespace


@pytest.fixture
def namespace():
    return Namespace(org_id=uuid4(), kg_id=uuid4())


CSV = (
    b"entity1,entity1_type,relation,entity2,entity2_type,pdf_name,source\n"
    b"Alpha,concept,uses,Beta,concept,a.pdf,legacy source A\n"
    b"Alpha,concept,uses,Beta,concept,b.pdf,legacy source B\n"
)


def test_phage_csv_retains_independent_source_ownership_and_scoped_stable_ids(namespace):
    graph = parse_legacy(namespace, "csv", CSV, origin="fixture-export")
    assert len(graph.relations) == 2
    assert {item.source_id for item in graph.relations} == {"a.pdf", "b.pdf"}
    assert graph.namespace == namespace
    assert graph == parse_legacy(namespace, "csv", CSV, origin="fixture-export")
    assert graph.raw_sha256 and graph.normalization_version
    assert all(item.status == "imported_unverified" for item in graph.relations)
    assert not graph.originals_verified
    other = parse_legacy(
        Namespace(org_id=namespace.org_id, kg_id=uuid4()), "csv", CSV, origin="fixture-export"
    )
    assert {item.id for item in graph.entities}.isdisjoint(item.id for item in other.entities)
    assert len({(item.source_id, item.legacy_id) for item in graph.entities}) == 4


def test_graphml_drops_unowned_aggregate_descriptions_and_rejects_dangling_edges(namespace):
    body = b"""<graphml xmlns="http://graphml.graphdrawing.org/xmlns">
    <key id="name" for="node" attr.name="entity_name" attr.type="string"/>
    <key id="description" for="node" attr.name="description" attr.type="string"/>
    <key id="source" for="edge" attr.name="source_id" attr.type="string"/>
    <key id="predicate" for="edge" attr.name="relation" attr.type="string"/>
    <graph edgedefault="directed"><node id="a"><data key="name">Alpha</data>
    <data key="description">private combined source description</data></node>
    <node id="b"><data key="name">Beta</data></node>
    <edge source="a" target="b"><data key="source">a.pdf</data>
    <data key="predicate">uses</data></edge></graph></graphml>"""
    graph = parse_legacy(namespace, "graphml", body, origin="graphml-fixture")
    assert len(graph.relations) == 1 and len(graph.entities) == 2
    assert "private combined" not in graph.model_dump_json()
    assert "AGGREGATED_DESCRIPTION_DROPPED" in graph.warnings
    with pytest.raises(DomainError):
        parse_legacy(
            namespace,
            "graphml",
            body.replace(b'target="b"', b'target="missing"'),
            origin="graphml-fixture",
        )
    with pytest.raises(DomainError):
        parse_legacy(
            namespace,
            "graphml",
            b'<!DOCTYPE x [<!ENTITY a SYSTEM "file:///etc/passwd">]><graphml>&a;</graphml>',
            origin="malicious",
        )


@pytest.mark.parametrize(
    "body",
    [
        b"entity1,relation,entity2\nAlpha,uses,Beta\n",
        CSV.replace(b"entity1,", b"org_id,entity1,", 1),
        b"x" * (2 * 1024 * 1024 + 1),
    ],
)
def test_invalid_or_oversize_csv_returns_safe_error(namespace, body):
    with pytest.raises(DomainError) as invalid:
        parse_legacy(namespace, "csv", body, origin="fixture-export")
    assert invalid.value.code in (ErrorCode.VALIDATION_ERROR, ErrorCode.QUOTA_EXCEEDED)
    assert "Alpha" not in invalid.value.message


def test_lightrag_manifest_requires_pinned_consistent_snapshot_and_known_sources(namespace):
    snapshot = {
        "format_version": 1,
        "engine_version": "1.5.7",
        "consistent": True,
        "documents": [{"id": "a.pdf", "sha256": "a" * 64}],
        "nodes": [
            {"id": "a", "name": "Alpha", "entity_type": "concept"},
            {"id": "b", "name": "Beta", "entity_type": "concept"},
        ],
        "edges": [
            {"id": "e", "source": "a", "target": "b", "predicate": "uses", "source_ids": ["a.pdf"]}
        ],
    }
    graph = parse_legacy(namespace, "lightrag", json.dumps(snapshot).encode(), origin="snapshot")
    assert len(graph.relations) == 1 and not graph.originals_verified
    for changes in ({"consistent": False}, {"engine_version": "0.0.0"}, {"org_id": str(uuid4())}):
        with pytest.raises(DomainError):
            parse_legacy(
                namespace, "lightrag", json.dumps(snapshot | changes).encode(), origin="snapshot"
            )
    snapshot["edges"][0]["source_ids"] = ["unknown.pdf"]
    with pytest.raises(DomainError):
        parse_legacy(namespace, "lightrag", json.dumps(snapshot).encode(), origin="snapshot")


def test_graphml_rejects_hidden_namespace_attributes_and_unsupported_nested_graphs(namespace):
    for extra in (' org_id="foreign"', ' kg_id="foreign"'):
        body = (
            '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">'
            '<graph edgedefault="directed"' + extra + "/></graphml>"
        ).encode()
        with pytest.raises(DomainError):
            parse_legacy(namespace, "graphml", body, origin="namespace-fixture")
    body = b"""<graphml xmlns="http://graphml.graphdrawing.org/xmlns">
        <graph edgedefault="directed"><node id="a"><graph edgedefault="directed"/>
        </node></graph></graphml>"""
    with pytest.raises(DomainError):
        parse_legacy(namespace, "graphml", body, origin="nested")


def test_orphan_legacy_nodes_are_retained_without_fabricated_source_text(namespace):
    body = b"""<graphml xmlns="http://graphml.graphdrawing.org/xmlns">
        <graph edgedefault="directed"><node id="a"/></graph></graphml>"""
    graph = parse_legacy(namespace, "graphml", body, origin="orphan")
    assert len(graph.entities) == 1 and graph.entities[0].source_id == "missing-original"
    assert not graph.relations and not graph.originals_verified


def test_source_fanout_is_bounded_before_building_all_projections(namespace):
    snapshot = {
        "format_version": 1,
        "engine_version": "1.5.7",
        "consistent": True,
        "documents": [{"id": f"doc-{index}", "sha256": "a" * 64} for index in range(100)],
        "nodes": [{"id": "a", "name": "Alpha"}, {"id": "b", "name": "Beta"}],
        "edges": [
            {
                "id": str(index),
                "source": "a",
                "target": "b",
                "predicate": "uses",
                "source_ids": [f"doc-{source}" for source in range(100)],
            }
            for index in range(11)
        ],
    }
    with pytest.raises(DomainError) as oversized:
        parse_legacy(namespace, "lightrag", json.dumps(snapshot).encode(), origin="fanout")
    assert oversized.value.code == ErrorCode.QUOTA_EXCEEDED


def test_opposite_snapshot_polarities_keep_distinct_assertion_identities(namespace):
    snapshot = {
        "format_version": 1,
        "engine_version": "1.5.7",
        "consistent": True,
        "documents": [{"id": "a.pdf", "sha256": "a" * 64}],
        "nodes": [{"id": "a", "name": "Alpha"}, {"id": "b", "name": "Beta"}],
        "edges": [
            {
                "id": polarity,
                "source": "a",
                "target": "b",
                "predicate": "uses",
                "source_ids": ["a.pdf"],
                "polarity": polarity,
            }
            for polarity in ("positive", "negative")
        ],
    }
    graph = parse_legacy(namespace, "lightrag", json.dumps(snapshot).encode(), origin="polarity")
    assert {relation.polarity for relation in graph.relations} == {"positive", "negative"}
    assert len({relation.assertion_id for relation in graph.relations}) == 2


def test_graphml_keeps_explicit_negative_polarity(namespace):
    body = b"""<graphml xmlns="http://graphml.graphdrawing.org/xmlns">
    <key id="p" for="edge" attr.name="polarity"/><key id="r" for="edge" attr.name="relation"/>
    <graph edgedefault="directed"><node id="Alpha"/><node id="Beta"/>
    <edge source="Alpha" target="Beta"><data key="r">uses</data>
    <data key="p">negative</data></edge></graph></graphml>"""
    graph = parse_legacy(namespace, "graphml", body, origin="polarity")
    assert graph.relations[0].polarity == "negative"
