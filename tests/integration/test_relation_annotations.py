"""Relationship descriptions annotate existing evidence without asserting new facts."""
# ruff: noqa: F811

import pytest
from test_curation import publish_curation
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.graph.service import GraphService
from domain_kg.retrieval.models import SearchFilters, SearchRequest, SubgraphRequest
from domain_kg.retrieval.service import RetrievalService
from domain_kg.revisions.models import RelationChanges, RelationUpdate
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


async def test_relation_annotation_filters_and_tags_preserve_original_claim(
    publishing, monkeypatch
):
    _, editor, queue, coordinator, _, _ = publishing
    sources = []
    for key in ("annotation-a", "annotation-b"):
        source, job, checkpoint = await prepare(publishing, key=key)
        await coordinator.publish(editor, await ready(publishing, job, checkpoint))
        sources.append(source)
    graph = GraphService(queue)
    scope = await graph.resolver.resolve(
        editor,
        SearchRequest(
            kg_ids=editor.kg_ids,
            query="Alpha",
            mode="graph",
        ),
        job.kg_id,
    )
    original = (await graph.repository.open(scope)).supports[0].assertion.assertion
    service = RevisionService(queue, coordinator.providers)
    command = RelationUpdate(
        kg_id=job.kg_id,
        assertion_id=original.id,
        expected_revision=0,
        source_ids=(sources[0].material_id,),
        changes=RelationChanges(description="Human relationship annotation"),
        reason="Describe the supported relationship",
        idempotency_key="relation-description",
    )
    receipt = await service.update_relation(editor, command)
    await publish_curation(queue, coordinator, editor, receipt)
    current = await graph.subgraph(
        editor,
        SubgraphRequest(
            kg_id=job.kg_id,
            seed_ids=(original.subject.id,),
        ),
    )
    assert len(current.edges) == 2 and all(edge.predicate == "uses" for edge in current.edges)
    a = next(edge for edge in current.edges if edge.source.material_id == sources[0].material_id)
    b = next(edge for edge in current.edges if edge.source.material_id == sources[1].material_id)
    assert a.description == command.changes.description and a.status == "verified" and a.spans
    assert a.annotations[0].curation.id == receipt.revision_id and b.description is None
    search = RetrievalService(queue, coordinator.providers)
    for filters in (SearchFilters(), SearchFilters(predicates=("uses",))):
        for mode in ("vector", "lexical", "graph", "deep"):
            response = await search.search(
                editor,
                SearchRequest(
                    kg_ids=editor.kg_ids,
                    query="Human relationship annotation",
                    mode=mode,
                    filters=filters,
                ),
            )
            manual = [hit for hit in response.results if hit.kind == "manual"]
            assert manual and manual[0].curation_revision_id == receipt.revision_id
            assert manual[0].version_id is None and manual[0].chunk_id is None
    for mode in ("vector", "lexical", "graph", "deep"):
        response = await search.search(
            editor,
            SearchRequest(
                kg_ids=editor.kg_ids,
                query="Human relationship annotation",
                mode=mode,
                filters=SearchFilters(support_statuses=("verified",)),
            ),
        )
        assert all(hit.kind != "manual" for hit in response.results)

    async def forbidden(*args, **kwargs):
        pytest.fail("relationship tags reuse the unchanged annotation vector")

    with monkeypatch.context() as patch:
        patch.setattr(coordinator.providers, "embeddings", forbidden)
        tags = await service.update_relation(
            editor,
            command.model_copy(
                update={
                    "expected_revision": 1,
                    "changes": RelationChanges(tags=("reviewed",)),
                    "idempotency_key": "relation-description-tags",
                }
            ),
        )
        await publish_curation(queue, coordinator, editor, tags)
    current = await graph.subgraph(
        editor,
        SubgraphRequest(
            kg_id=job.kg_id,
            seed_ids=(original.subject.id,),
        ),
    )
    assert len(current.edges) == 2
    a = next(edge for edge in current.edges if edge.source.material_id == sources[0].material_id)
    assert a.tags == ("reviewed",) and a.assertion_id == original.id
