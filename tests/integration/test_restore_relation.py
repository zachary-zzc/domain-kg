"""Changed namespace-dependent manual text obtains new approved target embeddings."""
# ruff: noqa: F811

import json

import httpx
import pytest
from test_curation import publish_curation
from test_publication import publishing  # noqa: F401
from test_restore_preparation import setup_restore

from domain_kg.errors import DomainError
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.operations.restore_activation import RestoreActivation
from domain_kg.operations.restore_authority import RestoreAuthority
from domain_kg.operations.restore_control import RestoreControl
from domain_kg.operations.restore_human_history import RestoreHumanHistory
from domain_kg.operations.restore_preparation import RestorePreparation
from domain_kg.operations.restore_projections import RestoreProjections
from domain_kg.operations.restore_stores import RestoreStores
from domain_kg.retrieval.models import SearchRequest, SubgraphRequest
from domain_kg.revisions.models import RelationChanges, RelationUpdate
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


@pytest.mark.parametrize("revoke", [False, True])
async def test_restored_manual_relation_reembeds_changed_text_without_extraction(
    publishing, team, catalog, monkeypatch, revoke
):
    _, editor, queue, coordinator, _, _ = publishing

    async def curate(source):
        graph = GraphService(queue)
        scope = await graph.resolver.resolve(
            editor, SearchRequest(kg_ids=(source.kg_id,), query="Alpha"), source.kg_id
        )
        claim = (await graph.repository.open(scope)).supports[0].assertion.assertion
        receipt = await RevisionService(queue, coordinator.providers).update_relation(
            editor,
            RelationUpdate(
                kg_id=source.kg_id,
                assertion_id=claim.id,
                expected_revision=0,
                source_ids=(source.material_id,),
                changes=RelationChanges(predicate="related_to"),
                reason="Human-reviewed meaning",
                idempotency_key="restore-manual-relation",
            ),
        )
        await publish_curation(queue, coordinator, editor, receipt)

    _, _, job, target, _ = await setup_restore(publishing, team, catalog, before_backup=curate)
    calls = []

    async def respond(request):
        assert request.url.path == "/v1/embeddings"
        payload = json.loads(request.content)
        calls.append(payload["input"])
        if revoke:
            registry = coordinator.providers.registry
            selected = binding.embedding
            profile = registry.profiles[selected.id]
            registry.profiles[selected.id] = profile.model_copy(update={"allowed_namespaces": ()})
        return httpx.Response(
            200,
            json={
                "model": payload["model"],
                "data": [
                    {"index": index, "embedding": [1.0, 0.0, 0.0]}
                    for index, _ in enumerate(payload["input"])
                ],
                "usage": {"prompt_tokens": 5},
            },
        )

    async def forbidden(*args, **kwargs):
        pytest.fail("restoration must never run extraction")

    monkeypatch.setattr(coordinator.providers, "transport", httpx.MockTransport(respond))
    monkeypatch.setattr(coordinator.providers, "extract", forbidden)
    binding = await RestoreStores(queue, coordinator.providers.registry).provision(job)
    try:
        if revoke:
            with pytest.raises(DomainError):
                await RestorePreparation(queue, coordinator.providers).prepare(job)
            assert len(calls) == 1
            assert await queue.database.kgs.find_one({"id": str(target.target.kg_id)}) is None
            return
        staged = await RestorePreparation(queue, coordinator.providers).prepare(job)
        assert len(calls) == 1 and len(calls[0]) == 1
        assert (
            str(target.target.kg_id) not in calls[0][0]
        )  # Text uses canonical entities, not a KG label.
        await RestoreAuthority(queue, coordinator.providers).stage(job, staged)
        await RestoreControl(queue, coordinator.providers).stage(job, staged)
        await RestoreHumanHistory(queue, coordinator.providers).stage(job, staged)
        parts = await RestoreProjections(queue, coordinator.providers).stage(job, staged)
        await RestoreActivation(queue, coordinator.providers).activate(job, staged, parts)
        access = await queue.policy.resolve_access(
            team[2]["owner"], [target.target.kg_id], Action.READ
        )
        alpha = next(
            row.observation.entity.id
            for row in staged.sources[0].document.rows
            if row.observation and row.observation.name == "Alpha"
        )
        graph = await GraphService(queue).subgraph(
            access, SubgraphRequest(kg_id=target.target.kg_id, seed_ids=(alpha,))
        )
        manual = [edge for edge in graph.edges if edge.predicate == "related_to"]
        assert len(manual) == 1 and manual[0].provenance.reason == "Human-reviewed meaning"
        assert not manual[0].spans
    finally:
        await binding.stores.vectors.client.delete_collection(binding.stores.vectors.collection)
        await binding.stores.graph.driver.execute_query(
            "MATCH (n {org_id:$org,kg_id:$kg}) DETACH DELETE n",
            org=str(target.target.org_id),
            kg=str(target.target.kg_id),
        )
