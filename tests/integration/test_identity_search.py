"""Every recall branch maps identities only in the claim's authorized source scope."""
# ruff: noqa: F811

import pytest
from test_identity_curation import finish_identity
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.graph.service import GraphService
from domain_kg.materials.lifecycle import MaterialLifecycle
from domain_kg.materials.models import RemoveMaterial
from domain_kg.retrieval.models import SearchFilters, SearchRequest
from domain_kg.retrieval.service import RetrievalService
from domain_kg.revisions.models import CurationCommand
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


async def test_all_search_modes_keep_source_qualified_identity_and_original_path(publishing):
    _, editor, queue, coordinator, _, _ = publishing
    sources = []
    for key in ("identity-source-a", "identity-source-b"):
        source, job, checkpoint = await prepare(publishing, key=key)
        await coordinator.publish(editor, await ready(publishing, job, checkpoint))
        sources.append(source)
    kg = job.kg_id
    graph = GraphService(queue)
    scope = await graph.resolver.resolve(editor, SearchRequest(kg_ids=(kg,), query="Alpha"), kg)
    data = await graph.repository.open(scope)
    alpha = next(item.entity.id for item in data.observations if item.name == "Alpha")
    beta = next(item.entity.id for item in data.observations if item.name == "Beta")
    revision = await RevisionService(queue, coordinator.providers).curate_node(
        editor,
        CurationCommand(
            kg_id=kg,
            entity_id=alpha,
            action="merge",
            member_ids=(alpha, beta),
            expected_revision=0,
            member_revisions=((beta, 0),),
            source_ids=(sources[0].material_id,),
            reason="Group only source A identities",
            idempotency_key="source-qualified-merge",
        ),
    )
    await finish_identity(queue, coordinator, editor, revision)
    retrieval = RetrievalService(queue, coordinator.providers)
    for source_index, source in enumerate(sources):
        for mode in ("fast", "deep", "vector", "lexical", "graph"):
            response = await retrieval.search(
                editor,
                SearchRequest(
                    kg_ids=(kg,),
                    query="Beta",
                    mode=mode,
                    filters=SearchFilters(material_ids=(source.material_id,)),
                ),
            )
            assert response.results, (source_index, mode)
            for item in response.results:
                assert item.material_id == source.material_id
                if source_index == 0:
                    assert set(item.entity_ids) == {alpha}
                    assert any(
                        witness.original_id == beta and witness.canonical_id == alpha
                        for witness in item.identity_mappings
                    )
                    for path in item.graph_paths:
                        assert path
                        assert all(step.subject_id == step.target_id == alpha for step in path)
                        assert all(step.original_target_id == beta for step in path)
                else:
                    assert set(item.entity_ids) == {alpha, beta}
                    assert item.identity_mappings == ()
    await MaterialLifecycle(publishing[0], queue).remove(
        editor,
        RemoveMaterial(
            kg_id=kg,
            material_id=sources[0].material_id,
            action="delete",
            expected_revision=1,
            idempotency_key="identity-remove-only-a",
        ),
    )
    independent = await graph.get_node(editor, kg, beta)
    assert independent.id == beta and independent.name == "Beta"
    assert independent.identity_mappings == ()
    assert {item.material_id for item in independent.observations} == {sources[1].material_id}
