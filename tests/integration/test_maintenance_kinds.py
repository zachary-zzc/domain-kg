"""Repair retains relation semantics and zero-vector identity history."""
# ruff: noqa: F811

import pytest
from qdrant_client import models
from test_curation import publish_curation
from test_identity_boundaries import pending_identity
from test_identity_curation import finish_identity
from test_publication import publishing  # noqa: F401

from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.maintenance import MaintenanceService
from domain_kg.operations.models import MaintenanceCommand
from domain_kg.retrieval.models import SearchRequest, SubgraphRequest
from domain_kg.revisions.models import RelationChanges, RelationUpdate, RollbackRevision
from domain_kg.revisions.projections import AcceptedProjection
from domain_kg.revisions.publication import accepted_document, projection_rows, verify_accepted

pytestmark = pytest.mark.storage


@pytest.mark.parametrize("kind", ["relation", "identity"])
async def test_relation_and_identity_repair_keep_history_and_rollback(
    publishing, team, monkeypatch, kind
):
    source, alpha, beta, revisions, merge = await pending_identity(publishing)
    _, editor, queue, coordinator, _, _ = publishing
    graph = GraphService(queue)
    if kind == "identity":
        receipt = await revisions.curate_node(editor, merge)
        await finish_identity(queue, coordinator, editor, receipt)
    else:
        scope = await graph.resolver.resolve(
            editor, SearchRequest(kg_ids=(source.kg_id,), query="Alpha"), source.kg_id
        )
        original = (await graph.repository.open(scope)).supports[0].assertion.assertion
        receipt = await revisions.update_relation(
            editor,
            RelationUpdate(
                kg_id=source.kg_id,
                assertion_id=original.id,
                expected_revision=0,
                source_ids=(source.material_id,),
                changes=RelationChanges(predicate="related_to", description="Reviewed relation"),
                reason="Preserve the reviewed relationship",
                idempotency_key="repair-relation",
            ),
        )
        await publish_curation(queue, coordinator, editor, receipt)

    async def forbidden(*args, **kwargs):
        pytest.fail("repair and metadata-only reversal must not call a model")

    monkeypatch.setattr(coordinator.providers, "embeddings", forbidden)
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.CONFIGURE)
    ns = {"org_id": str(source.org_id), "kg_id": str(source.kg_id)}
    counters = await queue.database.curation_targets.find(ns).to_list()
    current = await queue.database.kgs.find_one({"id": str(source.kg_id)})
    row = await queue.database.publication_bindings.find_one(
        ns
        | {
            "view_manifest_id": current["published_manifest_id"],
            "binding.curation.id": str(receipt.revision_id),
        }
    )
    parent = AcceptedProjection.model_validate(row["binding"])
    document, batch, _ = await accepted_document(queue, owner, parent)
    stores = await queue.router.for_kg(owner, source.kg_id)
    if document.rows:
        await stores.vectors.client.delete(
            stores.vectors.collection,
            points_selector=models.PointIdsList(
                points=[row["projection_id"] for row in projection_rows(document, batch)]
            ),
            wait=True,
        )
    await OwnerOperations(queue).submit(
        owner,
        MaintenanceCommand(
            kg_id=source.kg_id,
            expected_published_revision=2,
            curation_id=receipt.revision_id,
            idempotency_key="repair-other-kind",
        ),
    )
    await MaintenanceService(queue).run(await queue.claim("repair-kind", kinds=("maintenance",)))
    latest = await queue.database.kgs.find_one({"id": str(source.kg_id)})
    repaired = await queue.database.publication_bindings.find_one(
        ns | {"view_manifest_id": latest["published_manifest_id"], "owner_key": row["owner_key"]}
    )
    await verify_accepted(queue, owner, AcceptedProjection.model_validate(repaired["binding"]))
    assert await queue.database.curation_targets.find(ns).to_list() == counters
    if kind == "identity":
        node = await graph.get_node(editor, source.kg_id, beta)
        assert node.id == alpha and node.identity_mappings
    else:
        subgraph = await graph.subgraph(
            editor, SubgraphRequest(kg_id=source.kg_id, seed_ids=(alpha,))
        )
        assert any(
            edge.predicate == "related_to" and edge.curation_revision_id == receipt.revision_id
            for edge in subgraph.edges
        )
    rollback = await revisions.rollback(
        owner,
        RollbackRevision(
            kg_id=source.kg_id,
            revision_id=receipt.revision_id,
            expected_current_revision=1,
            reason="Reverse repaired history",
            idempotency_key="rollback-after-repair",
        ),
    )
    await publish_curation(queue, coordinator, owner, rollback)
    if kind == "identity":
        assert (await graph.get_node(owner, source.kg_id, beta)).id == beta
    else:
        subgraph = await graph.subgraph(
            owner, SubgraphRequest(kg_id=source.kg_id, seed_ids=(alpha,))
        )
        assert all(edge.predicate != "related_to" for edge in subgraph.edges)
