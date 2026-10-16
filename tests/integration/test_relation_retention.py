"""A source-owned human claim survives re-ingestion without reviving old labels."""

import pytest
from test_curation import publish_curation
from test_entity_resolution import batch_for
from test_publication import make_publishing, prepare, ready

from domain_kg.graph.service import GraphService
from domain_kg.materials.models import ReplaceMaterial
from domain_kg.retrieval.models import SearchFilters, SearchRequest, SubgraphRequest
from domain_kg.retrieval.service import RetrievalService
from domain_kg.revisions.models import NodeChanges, NodeUpdate, RelationChanges, RelationUpdate
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


@pytest.mark.parametrize("typed", [False, True])
async def test_source_owned_manual_claim_keeps_opaque_endpoints_after_reingestion(
    material_service, team, typed
):
    extraction = {"empty": False, "calls": 0}

    def extract(chunk, profile):
        extraction["calls"] += 1
        result = batch_for(chunk, profile)
        return (
            result.model_copy(update={"entities": (), "assertions": ()})
            if extraction["empty"]
            else result
        )

    fixture = await make_publishing(material_service, team, extraction_factory=extract)
    _, editor, queue, coordinator, _, _ = fixture
    source, job, checkpoint = await prepare(fixture, key="human-claim-source")
    await coordinator.publish(editor, await ready(fixture, job, checkpoint))
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
    receipt = await RevisionService(queue, coordinator.providers).update_relation(
        editor,
        RelationUpdate(
            kg_id=job.kg_id,
            assertion_id=original.id,
            expected_revision=0,
            source_ids=(source.material_id,),
            changes=RelationChanges(predicate="related_to"),
            reason="Independently reviewed human claim",
            idempotency_key="human-retained-claim",
        ),
    )
    await publish_curation(queue, coordinator, editor, receipt)
    if typed:
        node_revision = await RevisionService(queue, coordinator.providers).update_node(
            editor,
            NodeUpdate(
                kg_id=job.kg_id,
                entity_id=original.subject.id,
                expected_revision=0,
                source_ids=(source.material_id,),
                changes=NodeChanges(name="Reviewed source entity", entity_type="concept"),
                reason="Retain independently curated endpoint type",
                idempotency_key="human-retained-type",
            ),
        )
        await publish_curation(queue, coordinator, editor, node_revision)
    filters = SearchFilters(entity_types=("concept",)) if typed else SearchFilters()
    extraction["empty"] = True
    replacement, worker, checkpoint = await prepare(
        fixture,
        command=ReplaceMaterial(
            kg_id=job.kg_id,
            material_id=source.material_id,
            expected_active_version=source.version_id,
            title="New version with no extracted graph",
            inline_text="This version has no recognized entities or assertions.",
            idempotency_key="human-claim-source-replaced",
        ),
    )
    await coordinator.publish(editor, await ready(fixture, worker, checkpoint))
    assert replacement.version_id != source.version_id and extraction["calls"] == 2
    current = await graph.subgraph(
        editor,
        SubgraphRequest(
            kg_id=job.kg_id,
            seed_ids=(original.subject.id,),
            filters=filters,
        ),
    )
    assert len(current.edges) == 1 and current.edges[0].status == "manual"
    assert len(current.nodes) == 2 and all(not node.observations for node in current.nodes)
    if typed:
        reviewed = next(node for node in current.nodes if node.id == original.subject.id)
        assert reviewed.name == "Reviewed source entity" and reviewed.entity_types == ("concept",)
    else:
        assert all(node.name == str(node.id) for node in current.nodes)
    for mode in ("vector", "lexical", "graph", "deep"):
        response = await RetrievalService(queue, coordinator.providers).search(
            editor,
            SearchRequest(
                kg_ids=editor.kg_ids,
                query="related_to",
                mode=mode,
                filters=filters,
            ),
        )
        manual = [
            hit for hit in response.results if hit.curation_revision_id == receipt.revision_id
        ]
        assert manual
        assert all("Alpha" not in hit.text and "Beta" not in hit.text for hit in manual)
