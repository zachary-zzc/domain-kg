"""Import preparation preserves the export bytes and independently owned unverified rows."""

import hashlib
from uuid import uuid4

import pytest
from test_legacy_formats import CSV

from domain_kg.errors import DomainError
from domain_kg.graph.schema import schema_for
from domain_kg.imports.build import build_import, snapshot_document
from domain_kg.imports.legacy import parse_legacy
from domain_kg.imports.provenance import ImportOrigin
from domain_kg.jobs.models import Checkpoint
from domain_kg.materials.models import BlobRef
from domain_kg.models import Namespace


def fixture():
    ns = Namespace(org_id=uuid4(), kg_id=uuid4())
    blob = BlobRef(
        **ns.model_dump(),
        material_id=uuid4(),
        version_id=uuid4(),
        object_id=uuid4(),
        sha256=hashlib.sha256(CSV).hexdigest(),
        size_bytes=len(CSV),
        media_type="text/csv",
    )
    graph = parse_legacy(ns, "csv", CSV, origin="legacy-system")
    origin = ImportOrigin(
        **blob.model_dump(exclude={"object_id", "sha256", "size_bytes", "media_type"}),
        plan_id=uuid4(),
        plan_hash="a" * 64,
        graph_hash=hashlib.sha256(graph.model_dump_json().encode()).hexdigest(),
        origin=graph.origin,
    )
    checkpoint = Checkpoint(
        **ns.model_dump(),
        job_id=uuid4(),
        stage="chunking",
        artifact_id=uuid4(),
        input_hash="a" * 64,
        output_hash="b" * 64,
    )
    return ns, blob, graph, origin, checkpoint


def test_snapshot_chunks_are_byte_faithful_export_context_not_missing_document_quotes():
    _, blob, _, _, _ = fixture()
    document = snapshot_document(blob, CSV)
    assert document.blocks and document.chunks
    assert document.blocks[0].text == CSV.decode()
    assert document.chunks[0].text == CSV.decode()
    assert document.chunks[0].spans[0].quote == CSV.decode()
    assert document.chunks[0].spans[0].location.kind == "text"
    with pytest.raises(DomainError):
        snapshot_document(blob, CSV + b"changed")


def test_import_rows_have_version_owned_support_and_no_invented_document_provenance():
    ns, blob, graph, origin, checkpoint = fixture()
    parsed = snapshot_document(blob, CSV)
    prepared = build_import(graph, origin, parsed, schema_for(ns, "general"), checkpoint)
    supports = [row for row in prepared.rows if row.assertion]
    observations = [row for row in prepared.rows if row.observation]
    assert len(supports) == 2 and len(observations) == 4
    assert len({row.assertion.assertion.id for row in supports}) == 1
    assert len({row.id for row in supports}) == 2
    assert all(row.assertion.support.status == "imported_unverified" for row in supports)
    assert all(not row.assertion.support.spans for row in supports)
    assert all(
        not row.observation.spans and not row.observation.description for row in observations
    )
    assert all(row.source.version_id == blob.version_id for row in prepared.rows)
    newer_blob = blob.model_copy(update={"version_id": uuid4()})
    newer = build_import(
        graph,
        origin.model_copy(update={"version_id": newer_blob.version_id}),
        snapshot_document(newer_blob, CSV),
        schema_for(ns, "general"),
        checkpoint,
    )
    newer_supports = [row for row in newer.rows if row.assertion]
    assert {row.id for row in supports}.isdisjoint(row.id for row in newer_supports)
    assert {row.assertion.assertion.id for row in supports} == {
        row.assertion.assertion.id for row in newer_supports
    }
    with pytest.raises(DomainError):
        build_import(
            graph,
            origin.model_copy(update={"graph_hash": "0" * 64}),
            parsed,
            schema_for(ns, "general"),
            checkpoint,
        )


def test_content_derived_import_labels_remain_unverified_and_obey_closed_schemas():
    ns, blob, graph, origin, checkpoint = fixture()
    graph = graph.model_copy(
        update={
            "entities": tuple(
                e.model_copy(update={"entity_type": "assay_panel"}) for e in graph.entities
            ),
            "relations": tuple(
                r.model_copy(update={"predicate": "requires_sample"}) for r in graph.relations
            ),
        }
    )
    origin = origin.model_copy(
        update={
            "graph_hash": hashlib.sha256(graph.model_dump_json().encode()).hexdigest(),
        }
    )
    schema = schema_for(ns, "general")
    parsed = snapshot_document(blob, CSV)
    result = build_import(graph, origin, parsed, schema, checkpoint)
    supports = [row.assertion for row in result.rows if row.assertion]
    assert supports and all(row.assertion.predicate == "requires_sample" for row in supports)
    assert all(row.support.status == "imported_unverified" for row in supports)
    with pytest.raises(ValueError):
        build_import(
            graph, origin, parsed, schema.model_copy(update={"vocabulary": "closed"}), checkpoint
        )
