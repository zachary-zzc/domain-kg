"""Accepted import trust markers are reconciled before any default retrieval metadata."""
# ruff: noqa: F811

from uuid import uuid4

import pytest
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.imports.provenance import ImportOrigin
from domain_kg.models import SourceVersion
from domain_kg.revisions.journal import PublicationJournal

pytestmark = pytest.mark.storage


async def test_missing_import_marker_on_accepted_binding_fails_closed_before_visibility(publishing):
    _, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    origin = ImportOrigin(
        org_id=job.org_id,
        kg_id=job.kg_id,
        material_id=source.material_id,
        version_id=source.version_id,
        plan_id=uuid4(),
        plan_hash="b" * 64,
        graph_hash="c" * 64,
        origin="fixture",
    )
    # Simulate a lost binding extension while the authoritative origin ledger survives.
    await queue.database.import_source_origins.insert_one(origin.model_dump(mode="json"))
    with pytest.raises(DomainError) as stripped:
        await PublicationJournal(queue).load_metadata(editor, job.kg_id)
    assert stripped.value.code == ErrorCode.PROJECTION_INCONSISTENT
    assert origin.matches(
        SourceVersion(**{field: getattr(origin, field) for field in SourceVersion.model_fields})
    )


async def test_native_mix_and_managed_deep_keep_identical_permitted_fixture_ids(
    material_service, team
):
    from test_import_recovery import imported

    from domain_kg.engine.capabilities import checked_capabilities
    from domain_kg.engine.mix import candidate_native_id, native_mix
    from domain_kg.identity.models import Action
    from domain_kg.retrieval.graph import GraphRetrievalBranch
    from domain_kg.retrieval.models import SearchFilters, SearchRequest
    from domain_kg.retrieval.service import RetrievalService
    from domain_kg.retrieval.vector import VectorRetrievalBranch

    fixture, view = await imported(material_service, team)
    _, editor, queue, coordinator, _, _ = fixture
    kg = view.kg_id
    reader = await queue.policy.resolve_access(team[2]["reader"], [kg], Action.READ)
    vector = VectorRetrievalBranch(queue, coordinator.providers)
    graph = GraphRetrievalBranch(queue)
    filters = SearchFilters(support_statuses=("imported_unverified",))
    for actor, selected, expected in (
        (editor, SearchFilters(), (0, 0, 0)),
        (editor, filters, (2, 1, 1)),
        (reader, filters, (0, 0, 0)),
    ):
        request = SearchRequest(kg_ids=(kg,), query="Alpha", mode="deep", filters=selected)
        scope = await vector.resolver.resolve(actor, request, kg)
        vectors = await vector.retrieve(scope, request)
        recall = await graph.recall(scope, request)
        native = await native_mix(recall.graph, vectors)
        assert (
            len(native["final_entities"]),
            len(native["final_relations"]),
            len(native["vector_chunks"]),
        ) == expected
        assert (
            {item["entity_name"] for item in native["final_entities"]}
            == {str(node.id) for node in recall.graph.nodes}
            if recall.graph
            else not native["final_entities"]
        )
        assert {item["chunk_id"] for item in native["vector_chunks"]} == {
            candidate_native_id(item) for item in vectors
        }
        managed = await RetrievalService(queue, coordinator.providers).search(actor, request)
        assert {str(item.chunk_id) for item in managed.results} == {
            item["chunk_id"] for item in native["vector_chunks"]
        }
        assert all(item.support_status == "imported_unverified" for item in managed.results)
    capability = checked_capabilities()
    assert not capability.native_acl_filters and capability.scope_required
