"""Legacy restore rehashes typed provenance without promoting imported claims."""

import hashlib
from uuid import uuid4

import pytest

from domain_kg.errors import DomainError
from domain_kg.graph.models import DomainSchema, EntityRecord, PredicateDefinition
from domain_kg.imports.build import build_import, snapshot_document
from domain_kg.imports.legacy import parse_legacy
from domain_kg.imports.models import ImportPlan, ImportSource
from domain_kg.imports.provenance import ImportOrigin
from domain_kg.jobs.models import Checkpoint
from domain_kg.materials.models import BlobRef
from domain_kg.models import Namespace, SourceVersion
from domain_kg.operations.remapping import RestoreRemapper


def imported():
    source = Namespace(org_id=uuid4(), kg_id=uuid4())
    target = Namespace(org_id=source.org_id, kg_id=uuid4())
    version = SourceVersion(**source.model_dump(), material_id=uuid4(), version_id=uuid4())
    body = (
        b"entity1,entity1_type,relation,entity2,entity2_type,pdf_name,source\n"
        b"Alpha,concept,related_to,Beta,concept,demo,fixture\n"
    )
    blob = BlobRef(
        **version.model_dump(),
        object_id=uuid4(),
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
        media_type="text/csv",
    )
    schema = DomainSchema(
        **source.model_dump(),
        id=uuid4(),
        revision=1,
        entity_types=("concept",),
        predicates=(
            PredicateDefinition(
                name="related_to", subject_types=("concept",), target_types=("concept",)
            ),
        ),
    )
    graph = parse_legacy(source, "csv", body, origin=f"user origin {source.kg_id}")
    plan = ImportPlan(
        **source.model_dump(),
        id=uuid4(),
        content_hash=blob.sha256,
        source=ImportSource(
            kg_id=source.kg_id,
            upload_id=uuid4(),
            format="csv",
            origin=graph.origin,
            idempotency_key="historical-import",
        ),
        entities=len(graph.entities),
        assertions=len(graph.relations),
        schema_id=schema.id,
        schema_revision=1,
    )
    origin = ImportOrigin(
        **version.model_dump(),
        plan_id=plan.id,
        origin=graph.origin,
        plan_hash=hashlib.sha256(plan.model_dump_json().encode()).hexdigest(),
        graph_hash=hashlib.sha256(graph.model_dump_json().encode()).hexdigest(),
    )
    cp = Checkpoint(
        **source.model_dump(),
        job_id=uuid4(),
        stage="chunking",
        artifact_id=uuid4(),
        input_hash="a" * 64,
        output_hash="b" * 64,
    )
    prepared = build_import(graph, origin, snapshot_document(blob, body), schema, cp)
    observations = tuple(row.observation for row in prepared.rows if row.observation)
    facts = tuple(row.assertion.assertion for row in prepared.rows if row.assertion)
    mapping = RestoreRemapper(
        source,
        target,
        versions=(version,),
        entities=tuple(EntityRecord(**source.model_dump(), id=e.entity_id) for e in graph.entities),
        observations=observations,
        assertions=facts,
        legacy_graphs=(graph,),
    )
    return mapping, blob, plan, graph, origin, prepared, body


def test_import_plan_graph_and_origin_reseal_for_the_target_but_raw_bytes_do_not_change():
    from domain_kg.operations.restore_imports import restore_import

    mapping, blob, plan, graph, origin, _, body = imported()
    result = restore_import(mapping, plan, graph, origin, body)
    fresh = parse_legacy(mapping.target, "csv", body, origin=graph.origin)
    assert result.graph == fresh
    assert (
        result.plan.content_hash == blob.sha256
        and result.plan.support_status == "imported_unverified"
    )
    assert result.plan.source.upload_id == plan.source.upload_id
    assert result.origin.origin == origin.origin and result.original == origin
    assert (
        result.origin.plan_hash != origin.plan_hash
        and result.origin.graph_hash != origin.graph_hash
    )
    assert (
        result.origin.plan_hash
        == hashlib.sha256(result.plan.model_dump_json().encode()).hexdigest()
    )
    assert result.origin.graph_hash == hashlib.sha256(fresh.model_dump_json().encode()).hexdigest()
    assert result.origin.kg_id == mapping.target.kg_id
    with pytest.raises(DomainError):
        restore_import(
            mapping, plan, graph, origin.model_copy(update={"graph_hash": "0" * 64}), body
        )


def test_prepared_import_retains_unverified_source_ownership_with_resealed_origin():
    from domain_kg.operations.restore_documents import RestoreDocuments
    from domain_kg.operations.restore_imports import restore_import

    mapping, blob, plan, graph, origin, prepared, body = imported()
    bundle = restore_import(mapping, plan, graph, origin, body)
    rewrite = RestoreDocuments(mapping, originals=(blob,), imports=(bundle,))

    def checkpoint(cp):
        return cp.model_copy(update=mapping.target.model_dump())

    result = rewrite.prepared(prepared, checkpoint)
    assert result.import_origin == bundle.origin
    assert result.domain_schema.kg_id == mapping.target.kg_id
    assert all(row.source.kg_id == mapping.target.kg_id for row in result.rows)
    claims = [row.assertion for row in result.rows if row.assertion]
    assert claims and all(claim.support.status == "imported_unverified" for claim in claims)
    assert all(not claim.support.spans and claim.support.provenance is None for claim in claims)
    assert result.rows[0].chunk.text == prepared.rows[0].chunk.text
    with pytest.raises(DomainError):
        RestoreDocuments(mapping, originals=(blob,)).prepared(prepared, checkpoint)


def test_imported_vector_document_rebinds_hashes_without_elevating_claim_trust():
    from domain_kg.operations.restore_documents import RestoreDocuments
    from domain_kg.operations.restore_imports import restore_import
    from domain_kg.providers.embeddings import EmbeddingProfile
    from domain_kg.revisions.projections import EmbeddedDocument, ProjectionRow

    mapping, blob, plan, graph, origin, prepared, body = imported()
    bundle = restore_import(mapping, plan, graph, origin, body)
    rewrite = RestoreDocuments(mapping, originals=(blob,), imports=(bundle,))
    profile = EmbeddingProfile(
        **mapping.origin.model_dump(),
        id=uuid4(),
        revision=1,
        model="synthetic",
        dimensions=3,
        space_id="a" * 64,
    )
    resolved = prepared.chunk_checkpoint.model_copy(
        update={"stage": "resolving", "artifact_id": uuid4()}
    )
    source = SourceVersion(**{k: getattr(prepared, k) for k in SourceVersion.model_fields})
    document = EmbeddedDocument(
        **source.model_dump(),
        chunk_checkpoint=prepared.chunk_checkpoint,
        resolved_checkpoint=resolved,
        profile=profile,
        profile_selection_revision=1,
        schema_id=prepared.domain_schema.id,
        schema_revision=1,
        import_origin=origin,
        rows=tuple(
            ProjectionRow(**row.model_dump(), vector=(1.0, 0.0, 0.0)) for row in prepared.rows
        ),
    )
    result = rewrite.embedded(
        document,
        profile.model_copy(update=mapping.target.model_dump()),
        lambda cp: cp.model_copy(update=mapping.target.model_dump()),
    )
    assert result.import_origin == bundle.origin
    assert result.rows[0].text == document.rows[0].text
    assert all(
        row.assertion.support.status == "imported_unverified"
        for row in result.rows
        if row.assertion
    )


def test_import_artifact_chain_reseals_graph_and_prepared_rows_without_native_extraction():
    from test_restore_resealing import artifact

    from domain_kg.imports.prepared import PreparedImport
    from domain_kg.models import ScopedRef
    from domain_kg.operations.resealing import RestoreArtifactLedger
    from domain_kg.operations.restore_documents import RestoreDocuments
    from domain_kg.operations.restore_imports import restore_import

    mapping, blob, plan, graph, origin, prepared, body = imported()
    bundle = restore_import(mapping, plan, graph, origin, body)
    ledger = RestoreArtifactLedger(
        RestoreDocuments(mapping, originals=(blob,), imports=(bundle,)),
        ScopedRef(**mapping.origin.model_dump(), id=uuid4()),
    )
    parsed = snapshot_document(blob, body)
    job = prepared.chunk_checkpoint.job_id
    chunk_cp = None
    for stage, item, identifier in (
        ("validating", blob, None),
        ("parsing", parsed, None),
        ("chunking", parsed, prepared.chunk_checkpoint.artifact_id),
        ("extracting", graph, None),
    ):
        row, cp, data = artifact(blob, job, item, stage, identifier)
        ledger.rewrite(row, cp, data)
        if stage == "chunking":
            chunk_cp = cp
    prepared = PreparedImport.model_validate(
        prepared.model_copy(update={"chunk_checkpoint": chunk_cp})
    )
    row, cp, data = artifact(blob, job, prepared, "resolving")
    result = ledger.rewrite(row, cp, data)
    restored = PreparedImport.model_validate_json(result.body)
    assert restored.import_origin == bundle.origin
    assert all(
        r.assertion.support.status == "imported_unverified" for r in restored.rows if r.assertion
    )


def test_restored_import_owned_ids_match_future_reprocessing_without_duplicate_identities():
    from domain_kg.operations.restore_documents import RestoreDocuments
    from domain_kg.operations.restore_imports import restore_import

    mapping, blob, plan, graph, origin, prepared, body = imported()
    bundle = restore_import(mapping, plan, graph, origin, body)
    documents = RestoreDocuments(mapping, originals=(blob,), imports=(bundle,))

    def checkpoint(value):
        return value.model_copy(update=mapping.target.model_dump())

    rewritten = documents.prepared(prepared, checkpoint)
    fresh = build_import(
        bundle.graph,
        bundle.origin,
        documents.parsed(snapshot_document(blob, body)),
        documents.schema(prepared.domain_schema),
        checkpoint(prepared.chunk_checkpoint),
    )
    assert {row.id: row for row in rewritten.rows} == {row.id: row for row in fresh.rows}
