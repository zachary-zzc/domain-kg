"""Export encoding keeps source scope and treats labels as data in every format."""

import csv
import io
import json
from uuid import UUID, uuid4
from xml.etree import ElementTree

import pytest

from domain_kg.graph.views import NodeView
from domain_kg.models import Namespace


def node(kg, name):
    return NodeView(
        kg_id=kg,
        id=uuid4(),
        name=name,
        aliases=(),
        descriptions=(),
        entity_types=(),
        observations=(),
        observation_count=0,
        support_count=0,
        neighbor_count=0,
    )


def test_csv_export_neutralizes_spreadsheet_formulas_without_losing_scope():
    from domain_kg.operations.export_formats import encode_records

    ns = Namespace(org_id=uuid4(), kg_id=uuid4())
    value = node(ns.kg_id, '=HYPERLINK("https://example.invalid", "label")')
    body, media = encode_records(ns, (value,), (), "csv")
    rows = list(csv.DictReader(io.StringIO(body.decode())))
    assert media == "text/csv; charset=utf-8"
    assert rows[0]["name"].startswith("'=")
    assert rows[0]["org_id"] == str(ns.org_id) and rows[0]["kg_id"] == str(ns.kg_id)


def test_graphml_and_jsonl_escape_labels_and_preserve_unicode():
    from domain_kg.operations.export_formats import encode_records

    ns = Namespace(org_id=uuid4(), kg_id=uuid4())
    text = '<script>& "🔬测试"'
    value = node(ns.kg_id, text)
    body, _ = encode_records(ns, (value,), (), "graphml")
    root = ElementTree.fromstring(body)
    assert text in [element.text for element in root.iter()]
    assert b"<script>" not in body
    body, _ = encode_records(ns, (value,), (), "jsonl")
    record = json.loads(body)
    assert record["org_id"] == str(ns.org_id) and record["node"]["name"] == text


def test_export_rejects_a_node_from_another_kg():
    from domain_kg.errors import DomainError
    from domain_kg.operations.export_formats import encode_records

    ns = Namespace(org_id=uuid4(), kg_id=uuid4())
    with pytest.raises(DomainError):
        encode_records(ns, (node(uuid4(), "private"),), (), "jsonl")


def test_graphml_rejects_text_that_xml_cannot_represent():
    from domain_kg.errors import DomainError
    from domain_kg.operations.export_formats import encode_records

    ns = Namespace(org_id=uuid4(), kg_id=uuid4())
    with pytest.raises(DomainError):
        encode_records(ns, (node(ns.kg_id, "invalid\x00label"),), (), "graphml")


def test_namespace_subclasses_cannot_add_unselected_context_to_records():
    from domain_kg.operations.export_formats import encode_records

    class PrivateContext(Namespace):
        private_material_id: UUID

    ns = PrivateContext(org_id=uuid4(), kg_id=uuid4(), private_material_id=uuid4())
    for format in ("jsonl", "csv", "graphml"):
        body, _ = encode_records(ns, (node(ns.kg_id, "Selected"),), (), format)
        assert str(ns.private_material_id).encode() not in body
