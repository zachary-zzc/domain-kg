"""Request boundaries, immutable read scopes, and evidence provenance."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError


@pytest.mark.parametrize(
    "changes",
    [
        {"query": "   "},
        {"query": "q" * 8001},
        {"kg_ids": []},
        {"kg_ids": [uuid4() for _ in range(11)]},
        {"top_k": 0},
        {"top_k": 51},
        {"top_k": True},
        {"max_hops": -1},
        {"max_hops": 3},
        {"max_nodes": 0},
        {"max_nodes": 201},
        {"max_edges": -1},
        {"max_edges": 401},
        {"max_context_chars": 999},
        {"max_context_chars": 48001},
        {"mode": "hybrid"},
        {"rerank": True},
        {"generate_keywords": True},
        {"user_id": "forged"},
        {"role": "owner"},
    ],
)
def test_search_rejects_invalid_limits_modes_and_identity_claims(changes) -> None:
    from domain_kg.contracts import SearchRequest

    values = {"kg_ids": [uuid4()], "query": "q"} | changes
    with pytest.raises(ValidationError):
        SearchRequest.model_validate(values)


def test_single_character_and_deep_options_are_supported() -> None:
    from domain_kg.contracts import SearchRequest

    request = SearchRequest(kg_ids=[uuid4()], query=" 中 ")
    assert request.query == "中"
    assert request.mode == "fast" and request.effective_hops == 0
    deep = request.model_copy(update={"mode": "deep", "rerank": True, "generate_keywords": True})
    assert deep.effective_hops == 2


def test_explicit_version_requires_one_material_and_kg() -> None:
    from domain_kg.contracts import SearchRequest

    version, material, kg = uuid4(), uuid4(), uuid4()
    for kgs, materials in [([kg], []), ([kg, uuid4()], [material]), ([kg], [material, uuid4()])]:
        with pytest.raises(ValidationError):
            SearchRequest(
                kg_ids=kgs, query="q", filters={"version_id": version, "material_ids": materials}
            )
    request = SearchRequest(
        kg_ids=[kg], query="q", filters={"version_id": version, "material_ids": [material]}
    )
    assert request.filters.version_id == version


def test_filters_use_or_within_lists_and_and_across_fields_and_half_open_time() -> None:
    from domain_kg.contracts import MaterialMetadata, SearchFilters

    material = uuid4()
    metadata = MaterialMetadata(
        material_id=material,
        version_id=uuid4(),
        media_type="text/plain",
        tags=("design", "review"),
        author="Ada",
        source="work",
        language="en",
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
        updated_at=datetime(2026, 10, 2, tzinfo=UTC),
    )
    filters = SearchFilters(
        material_ids=[uuid4(), material],
        media_types=["text/plain", "application/pdf"],
        tags_all=["design", "review"],
        tags_any=["missing", "review"],
        authors=["Ada", "Bob"],
        created_from=datetime(2026, 10, 1, tzinfo=UTC),
        created_before=datetime(2026, 10, 2, tzinfo=UTC),
    )
    assert filters.matches_material(metadata)
    assert not filters.model_copy(update={"languages": ("zh",)}).matches_material(metadata)
    assert not filters.model_copy(update={"tags_all": ("design", "missing")}).matches_material(
        metadata
    )
    assert not filters.model_copy(update={"created_before": metadata.created_at}).matches_material(
        metadata
    )
    assert not filters.model_copy(update={"exclude_material_ids": (material,)}).matches_material(
        metadata
    )


@pytest.mark.parametrize(
    "filters",
    [
        {"min_confidence": -0.01},
        {"min_confidence": 1.01},
        {"support_statuses": ["rejected"]},
        {"created_from": "2026-10-01T00:00:00"},
        {"created_from": "2026-10-02T00:00:00Z", "created_before": "2026-10-01T00:00:00Z"},
        {"unsupported_filter": True},
    ],
)
def test_filters_reject_invalid_confidence_times_and_unknown_fields(filters) -> None:
    from domain_kg.contracts import SearchFilters

    with pytest.raises(ValidationError):
        SearchFilters.model_validate(filters)


def test_text_only_chunks_and_sourceless_manual_evidence_have_distinct_eligibility() -> None:
    from domain_kg.contracts import SearchFilters

    default = SearchFilters()
    assert not default.requires_assertion_support
    assert default.allows_sourceless_manual
    assert SearchFilters(predicates=["CITES"]).requires_assertion_support
    assert SearchFilters(predicates=["CITES"]).allows_sourceless_manual
    assert not SearchFilters(tags_any=["design"]).allows_sourceless_manual
    assert not SearchFilters(support_statuses=["verified"]).allows_sourceless_manual


def test_source_and_manual_evidence_do_not_invent_each_others_provenance() -> None:
    from domain_kg.contracts import EvidenceCandidate

    base = {"kg_id": uuid4(), "text": "A documented claim", "citation_ids": [uuid4()]}
    with pytest.raises(ValidationError):
        EvidenceCandidate.model_validate(base | {"kind": "source"})
    with pytest.raises(ValidationError):
        EvidenceCandidate.model_validate(base | {"kind": "manual", "curation_revision_id": uuid4()})
    manual = EvidenceCandidate.model_validate(
        base
        | {
            "kind": "manual",
            "curation_revision_id": uuid4(),
            "provenance": {"actor": "issuer|subject", "reason": "Explicit correction"},
        }
    )
    assert manual.material_id is None and manual.chunk_id is None
    source = EvidenceCandidate.model_validate(
        base
        | {
            "kind": "source",
            "material_id": uuid4(),
            "version_id": uuid4(),
            "chunk_id": uuid4(),
        }
    )
    assert source.curation_revision_id is None


def test_edits_require_revision_idempotency_and_typed_changes() -> None:
    from domain_kg.contracts import NodeUpdate

    values = {
        "kg_id": uuid4(),
        "entity_id": uuid4(),
        "reason": "Rename",
        "changes": {"name": "Alpha"},
    }
    for extra in [{}, {"expected_revision": 0}, {"expected_revision": 0, "idempotency_key": " "}]:
        with pytest.raises(ValidationError):
            NodeUpdate.model_validate(values | extra)
    command = NodeUpdate.model_validate(
        values | {"expected_revision": 0, "idempotency_key": "edit-1"}
    )
    assert command.changes.name == "Alpha"
    with pytest.raises(ValidationError):
        NodeUpdate.model_validate(
            values
            | {
                "expected_revision": 0,
                "idempotency_key": "edit-1",
                "changes": {"$set": {"role": "owner"}},
            }
        )


def test_read_scope_rejects_unaccepted_sources_and_is_immutable() -> None:
    from domain_kg.contracts import (
        AccessContext,
        Principal,
        PublishedView,
        ReadScope,
        SourceVersion,
    )

    org, kg = uuid4(), uuid4()
    source = SourceVersion(org_id=org, kg_id=kg, material_id=uuid4(), version_id=uuid4())
    principal = Principal(issuer="https://idp.example", subject="reader", scopes=["domain_kg:read"])
    access = AccessContext(principal=principal, org_id=org, kg_ids=[kg], policy_revision=1)
    view = PublishedView(org_id=org, kg_id=kg, revision=1, generation=uuid4(), sources=[source])
    scope = ReadScope(access=access, view=view, visible_sources=[source], visibility_revision=1)
    with pytest.raises(ValidationError):
        scope.visibility_revision = 2
    assert isinstance(scope.visible_sources, tuple)
    with pytest.raises(ValidationError):
        ReadScope(
            access=access,
            view=view,
            visible_sources=[source.model_copy(update={"version_id": uuid4()})],
            visibility_revision=1,
        )


def test_source_text_and_span_offsets_preserve_whitespace() -> None:
    from domain_kg.ingestion.models import Span
    from domain_kg.materials.models import MaterialInput

    original = "  exact source\n"
    span = Span(
        location={"kind": "text", "block_id": uuid4()}, start=0, end=len(original), quote=original
    )
    assert span.quote == original
    assert MaterialInput(inline_text=original).inline_text == original


@pytest.mark.parametrize("limit", [0, 101, True])
def test_list_limits_are_bounded(limit) -> None:
    from domain_kg.contracts import PageRequest

    with pytest.raises(ValidationError):
        PageRequest(limit=limit)


def test_upload_parser_and_graph_caps_without_large_allocations() -> None:
    from domain_kg.contracts import BlobRef, ParseOptions, SubgraphRequest

    blob = dict(
        org_id=uuid4(),
        kg_id=uuid4(),
        material_id=uuid4(),
        version_id=uuid4(),
        object_id=uuid4(),
        sha256="0" * 64,
        media_type="text/plain",
    )
    assert BlobRef(**blob, size_bytes=50 * 1024 * 1024).size_bytes == 52428800
    with pytest.raises(ValidationError):
        BlobRef(**blob, size_bytes=50 * 1024 * 1024 + 1)
    with pytest.raises(ValidationError):
        ParseOptions(max_pages=501)
    with pytest.raises(ValidationError):
        SubgraphRequest(kg_id=uuid4(), seed_ids=[uuid4(), uuid4()], max_nodes=1)


def test_readiness_receipts_bind_exact_attempt_and_required_backends() -> None:
    from domain_kg.contracts import ProjectionReceipt, ReadinessReport, RevisionBatch

    batch = RevisionBatch(
        org_id=uuid4(),
        kg_id=uuid4(),
        revision_id=uuid4(),
        attempt_id=uuid4(),
        generation=uuid4(),
        manifest_id=uuid4(),
        content_hash="0" * 64,
        expected_revision=0,
        records=[],
    )
    receipt_values = {
        key: getattr(batch, key)
        for key in ("org_id", "kg_id", "attempt_id", "generation", "manifest_id", "content_hash")
    }
    receipts = [
        ProjectionReceipt(**receipt_values, backend=backend, record_count=1, checksum="1" * 64)
        for backend in ("graph", "vector", "lexical")
    ]
    assert ReadinessReport(batch=batch, receipts=receipts, ready=True).ready
    with pytest.raises(ValidationError):
        ReadinessReport(batch=batch, receipts=receipts[:1], ready=True)
    with pytest.raises(ValidationError):
        ReadinessReport(
            batch=batch,
            receipts=[receipts[0].model_copy(update={"attempt_id": uuid4()})],
            ready=False,
        )
