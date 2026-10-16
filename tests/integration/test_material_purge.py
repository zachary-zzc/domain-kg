"""Retention cleanup is exact-source work, never a bucket or shared-fact delete."""
# ruff: noqa: F811

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from test_curation import publish_curation
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.materials.lifecycle import MaterialLifecycle
from domain_kg.materials.models import RemoveMaterial, RestoreMaterial
from domain_kg.materials.purge_models import PurgeMaterial
from domain_kg.retrieval.models import SearchFilters, SearchRequest, SubgraphRequest
from domain_kg.retrieval.service import RetrievalService
from domain_kg.revisions.journal import PublicationJournal
from domain_kg.revisions.models import NodeChanges, NodeUpdate, RelationChanges, RelationUpdate
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


async def test_purge_expired_source_preserves_independent_support_and_replays(publishing, team):
    materials, editor, queue, coordinator, _, _ = publishing
    sources = []
    for key in ("purge-source-a", "purge-source-b"):
        source, job, checkpoint = await prepare(publishing, key=key)
        await coordinator.publish(editor, await ready(publishing, job, checkpoint))
        sources.append(source)
    first, second = sources
    kg = job.kg_id
    graph_service = GraphService(queue)
    scope = await graph_service.resolver.resolve(
        editor, SearchRequest(kg_ids=(kg,), query="Alpha"), kg
    )
    original_data = await graph_service.repository.open(scope)
    alpha = next(item.entity.id for item in original_data.observations if item.name == "Alpha")
    human = await RevisionService(queue, coordinator.providers).update_node(
        editor,
        NodeUpdate(
            kg_id=kg,
            entity_id=alpha,
            expected_revision=0,
            source_ids=(second.material_id,),
            changes=NodeChanges(name="Reviewed B"),
            reason="Keep B's independent human correction",
            idempotency_key="purge-b-human",
        ),
    )
    await publish_curation(queue, coordinator, editor, human)
    owner = await queue.policy.resolve_access(team[2]["owner"], [kg], Action.PURGE)
    lifecycle = MaterialLifecycle(materials, queue)
    removed = await lifecycle.remove(
        editor,
        RemoveMaterial(
            kg_id=kg,
            material_id=first.material_id,
            expected_revision=1,
            action="delete",
            idempotency_key="purge-tombstone",
        ),
    )
    command = PurgeMaterial(
        kg_id=kg,
        material_id=first.material_id,
        expected_revision=removed.revision,
        reason="Expired fixture retention",
        idempotency_key="purge-expired-source",
    )
    with pytest.raises(DomainError):
        await lifecycle.purge(owner, command)
    ns = {"org_id": str(editor.org_id), "kg_id": str(kg)}
    await queue.database.material_access.update_one(
        ns | {"id": str(first.material_id)},
        {"$set": {"purge_after": datetime.now(UTC) - timedelta(seconds=1)}},
    )
    with pytest.raises(DomainError):
        await lifecycle.purge(editor, command)
    before = await queue.database.jobs.count_documents(ns)
    dry = await lifecycle.purge(owner, command.model_copy(update={"dry_run": True}))
    assert dry.state == "preview" and dry.impact.versions == 1
    assert await queue.database.jobs.count_documents(ns) == before
    view = await PublicationJournal(queue).load_metadata(owner, kg)
    old_a = next(
        binding
        for binding in view.projections
        if binding.source and binding.source.material_id == first.material_id
    )
    old_b = next(
        binding
        for binding in view.projections
        if binding.source and binding.source.material_id == second.material_id
    )
    pending = await lifecycle.purge(owner, command)
    assert pending.state == "pending" and pending.job_id
    assert await lifecycle.purge(owner, command) == pending
    purge = await queue.claim("source-purger", kinds=("purge_material",))
    assert purge and purge.id == pending.job_id
    await JobRunner(queue, configured_pipeline(queue, coordinator.providers)).run_job(purge)
    assert (await queue.get(owner, purge.id)).stage == "ready"
    completed = await lifecycle.purge(owner, command)
    assert completed.state == "purged" and completed.job_id == pending.job_id
    assert await lifecycle.purge(owner, command) == completed
    with pytest.raises(DomainError):
        await lifecycle.restore(
            owner,
            RestoreMaterial(
                kg_id=kg,
                material_id=first.material_id,
                expected_revision=completed.revision,
                idempotency_key="cannot-restore-purged-source",
            ),
        )
    stores = await queue.router.for_sources(owner, kg)
    for collection in ("source_blocks", "source_chunks", "observations"):
        assert (
            await stores.authority._database[collection].count_documents(
                ns | {"material_id": str(first.material_id)}
            )
            == 0
        )
    graph = await queue.router.for_graph(owner, kg)
    for binding, expected in ((old_a, 0), (old_b, None)):
        rows, _, _ = await graph.driver.execute_query(
            "MATCH (n:ScopedProjection {org_id:$org,kg_id:$kg,attempt_id:$attempt}) "
            "RETURN count(n) AS total",
            org=str(editor.org_id),
            kg=str(kg),
            attempt=str(binding.attempt_id),
        )
        assert rows[0]["total"] == 0 if expected == 0 else rows[0]["total"] > 0
    # No object of this source survives, while B's original and accepted view remain usable.
    listing = await asyncio.to_thread(
        stores.blobs.client.list_objects_v2,
        Bucket=stores.blobs.bucket,
        Prefix=f"{editor.org_id}/{kg}/{first.material_id}/",
    )
    assert listing.get("KeyCount", 0) == 0
    assert b"".join(
        [
            part
            async for part in materials.original(owner, kg, second.material_id, second.version_id)
        ]
    )
    search = RetrievalService(queue, coordinator.providers)
    result = await search.search(owner, SearchRequest(kg_ids=(kg,), query="Alpha", mode="deep"))
    assert result.results and {hit.material_id for hit in result.results} == {second.material_id}
    retained = await search.search(
        owner,
        SearchRequest(
            kg_ids=(kg,),
            query="Alpha",
            mode="deep",
            filters=SearchFilters(material_ids=(second.material_id,), version_id=second.version_id),
        ),
    )
    assert retained.results and {hit.material_id for hit in retained.results} == {
        second.material_id
    }
    graph_service = GraphService(queue)
    scope = await graph_service.resolver.resolve(
        owner, SearchRequest(kg_ids=(kg,), query="Alpha"), kg
    )
    data = await graph_service.repository.open(scope)
    alpha = next(item.entity.id for item in data.observations if item.name == "Alpha")
    node = await graph_service.get_node(owner, kg, alpha)
    assert node.support_count == 1 and node.name == "Reviewed B"
    assert human.revision_id in {item.curation.id for item in node.curations}


async def test_public_human_claim_survives_owning_original_purge(publishing, team):
    materials, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    kg = job.kg_id
    graph = GraphService(queue)
    scope = await graph.resolver.resolve(editor, SearchRequest(kg_ids=(kg,), query="Alpha"), kg)
    original = (await graph.repository.open(scope)).supports[0].claim.assertion
    owner = await queue.policy.resolve_access(team[2]["owner"], [kg], Action.CURATE)
    human = await RevisionService(queue, coordinator.providers).update_relation(
        owner,
        RelationUpdate(
            kg_id=kg,
            assertion_id=original.id,
            expected_revision=0,
            curation_scope="kg_public",
            changes=RelationChanges(
                subject_id=original.subject.id,
                target_id=original.target.id,
                predicate="related_to",
                description="Public human claim",
            ),
            reason="Independent public claim",
            idempotency_key="purge-public-claim",
        ),
    )
    await publish_curation(queue, coordinator, owner, human)
    lifecycle = MaterialLifecycle(materials, queue)
    removed = await lifecycle.remove(
        editor,
        RemoveMaterial(
            kg_id=kg,
            material_id=source.material_id,
            expected_revision=1,
            action="delete",
            idempotency_key="purge-public-original",
        ),
    )
    await queue.database.material_access.update_one(
        {"org_id": str(editor.org_id), "kg_id": str(kg), "id": str(source.material_id)},
        {"$set": {"purge_after": datetime.now(UTC) - timedelta(seconds=1)}},
    )
    command = PurgeMaterial(
        kg_id=kg,
        material_id=source.material_id,
        expected_revision=removed.revision,
        reason="Expired public-claim source",
        idempotency_key="purge-public-original-bytes",
    )
    await lifecycle.purge(owner, command)
    purge = await queue.claim("public-claim-purger", kinds=("purge_material",))
    await JobRunner(queue, configured_pipeline(queue, coordinator.providers)).run_job(purge)
    assert (await lifecycle.purge(owner, command)).state == "purged"
    current = await graph.subgraph(
        owner, SubgraphRequest(kg_id=kg, seed_ids=(original.subject.id,))
    )
    assert len(current.edges) == 1 and current.edges[0].status == "manual"
    assert all(not node.observations and node.name == str(node.id) for node in current.nodes)
    for mode in ("vector", "lexical", "graph", "deep"):
        found = await RetrievalService(queue, coordinator.providers).search(
            owner, SearchRequest(kg_ids=(kg,), query="Public human claim", mode=mode)
        )
        assert found.results and all(
            hit.curation_revision_id == human.revision_id for hit in found.results
        )
        assert all(
            hit.material_id is None and "Alpha" not in hit.text and "Beta" not in hit.text
            for hit in found.results
        )
