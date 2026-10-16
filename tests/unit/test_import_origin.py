"""Import provenance remains source-scoped and cannot silently mark claims verified."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_kg.imports.provenance import ImportOrigin
from domain_kg.models import SourceVersion
from domain_kg.revisions.projections import AcceptedProjection


def test_accepted_import_origin_matches_exact_source_and_native_serialization_stays_stable():
    source = SourceVersion(org_id=uuid4(), kg_id=uuid4(), material_id=uuid4(), version_id=uuid4())
    original = dict(
        org_id=source.org_id,
        kg_id=source.kg_id,
        source=source,
        attempt_id=uuid4(),
        manifest_id=uuid4(),
        content_hash="a" * 64,
        generation=uuid4(),
        chunk_artifact_id=uuid4(),
    )
    native = AcceptedProjection(**original)
    assert "import_origin" not in native.model_dump(mode="json")
    origin = ImportOrigin(
        **source.model_dump(),
        plan_id=uuid4(),
        plan_hash="b" * 64,
        graph_hash="c" * 64,
        origin="legacy-system",
    )
    imported = AcceptedProjection(**original, import_origin=origin)
    assert imported.import_origin == origin
    assert (
        imported.model_dump(mode="json")["import_origin"]["support_status"] == "imported_unverified"
    )
    with pytest.raises(ValidationError):
        AcceptedProjection(**original, import_origin=origin.model_copy(update={"kg_id": uuid4()}))
    with pytest.raises(ValidationError):
        AcceptedProjection(
            **original, import_origin=origin.model_copy(update={"version_id": uuid4()})
        )
    with pytest.raises(ValidationError):
        ImportOrigin.model_validate(origin.model_dump() | {"support_status": "verified"})


def test_imported_embedded_rows_never_gain_verified_or_manual_support():
    import hashlib

    from test_identity_graph_views import graph_fixture

    from domain_kg.jobs.models import Checkpoint
    from domain_kg.providers.embeddings import EmbeddingProfile
    from domain_kg.revisions.projections import EmbeddedDocument, ProjectionRow

    data, *_ = graph_fixture()
    source = data.scope.visible_sources[0]
    namespace = source.model_dump(include={"org_id", "kg_id"})
    origin = ImportOrigin(
        **source.model_dump(),
        plan_id=uuid4(),
        plan_hash="b" * 64,
        graph_hash="c" * 64,
        origin="legacy",
    )
    checkpoint = Checkpoint(
        **namespace,
        job_id=uuid4(),
        artifact_id=uuid4(),
        stage="chunking",
        input_hash="a" * 64,
        output_hash="a" * 64,
    )
    rows = tuple(
        ProjectionRow(**row.model_dump(), vector=(1.0, 0.0, 0.0))
        for row in data.rows
        if row.source == source
    )
    document = EmbeddedDocument(
        **source.model_dump(),
        chunk_checkpoint=checkpoint,
        resolved_checkpoint=checkpoint.model_copy(update={"stage": "resolving"}),
        profile=EmbeddingProfile(
            **namespace, id=uuid4(), revision=1, model="fixture", dimensions=3
        ),
        profile_selection_revision=1,
        schema_id=uuid4(),
        schema_revision=1,
        rows=rows,
    )
    raw = document.model_dump_json()
    assert "import_origin" not in raw
    restored = EmbeddedDocument.model_validate_json(raw)
    assert (
        hashlib.sha256(restored.model_dump_json().encode()).digest()
        == hashlib.sha256(raw.encode()).digest()
    )
    with pytest.raises(ValidationError):
        EmbeddedDocument.model_validate(document.model_copy(update={"import_origin": origin}))
    unverified = tuple(
        row.model_copy(
            update={
                "assertion": row.assertion.model_copy(
                    update={
                        "support": row.assertion.support.model_copy(
                            update={"status": "imported_unverified", "spans": ()}
                        )
                    }
                )
            }
        )
        if row.assertion
        else row
        for row in rows
    )
    imported = EmbeddedDocument.model_validate(
        document.model_copy(
            update={
                "import_origin": origin,
                "rows": unverified,
            }
        )
    )
    assert imported.import_origin == origin


async def test_default_scope_excludes_all_import_source_fields_before_branch_predicates():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from test_identity_graph_views import graph_fixture

    from domain_kg.engine.scope import EligibilityResolver, ScopePredicates
    from domain_kg.materials.models import MaterialMetadata
    from domain_kg.retrieval.models import SearchFilters, SearchRequest
    from domain_kg.revisions.models import PublishedView

    data, *_ = graph_fixture()
    imported, native = data.scope.view.sources
    origin = ImportOrigin(
        **imported.model_dump(),
        plan_id=uuid4(),
        plan_hash="b" * 64,
        graph_hash="c" * 64,
        origin="legacy",
    )
    bindings = tuple(
        binding.model_copy(update={"import_origin": origin})
        if binding.source == imported
        else binding
        for binding in data.scope.view.projections
        if binding.source
    )
    view = PublishedView.model_validate(
        data.scope.view.model_copy(
            update={
                "projections": bindings,
                "curations": (),
            }
        )
    )
    now = datetime.now(UTC)
    rows = {
        str(source.material_id): {
            "active_version_id": str(source.version_id),
            "metadata": MaterialMetadata(
                material_id=source.material_id,
                version_id=source.version_id,
                media_type="text/csv",
                created_at=now,
                updated_at=now,
            ).model_dump(mode="json"),
        }
        for source in view.sources
    }

    async def material(query):
        return rows[query["id"]]

    policy = SimpleNamespace(
        revalidate=AsyncMock(return_value=data.scope.access),
        material_visible=AsyncMock(return_value=True),
        database=SimpleNamespace(
            kgs=SimpleNamespace(find_one=AsyncMock(return_value={"visibility_revision": 1})),
            material_access=SimpleNamespace(find_one=material),
        ),
    )
    views = SimpleNamespace(
        load=AsyncMock(return_value=view), node_fences=AsyncMock(return_value=())
    )
    resolver = EligibilityResolver(policy, views)
    for mode in ("vector", "lexical", "graph", "fast", "deep"):
        scope = await resolver.resolve(
            data.scope.access,
            SearchRequest(
                kg_ids=(view.kg_id,),
                query="Alpha",
                mode=mode,
            ),
            view.kg_id,
        )
        assert scope.visible_sources == (native,)
        predicates = ScopePredicates(scope)
        assert str(imported.version_id) not in predicates.graph_parameters["version_ids"]
        assert str(imported.version_id) not in predicates.vector_filter.model_dump_json()
    explicit = await resolver.resolve(
        data.scope.access,
        SearchRequest(
            kg_ids=(view.kg_id,),
            query="Alpha",
            filters=SearchFilters(support_statuses=("imported_unverified",)),
        ),
        view.kg_id,
    )
    assert set(explicit.visible_sources) == {imported, native}
    policy.material_visible.side_effect = lambda access, kg, material: (
        material != imported.material_id
    )
    denied = await resolver.resolve(
        data.scope.access,
        SearchRequest(
            kg_ids=(view.kg_id,),
            query="Alpha",
            filters=SearchFilters(support_statuses=("imported_unverified",)),
        ),
        view.kg_id,
    )
    assert denied.visible_sources == (native,)
