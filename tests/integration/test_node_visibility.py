"""Node fences apply before recall and cannot be removed by stale publishers."""
# ruff: noqa: F811

import pytest
from test_curation import alpha_id
from test_entity_resolution import TEXT
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError
from domain_kg.graph.service import GraphService
from domain_kg.materials.models import ReplaceMaterial
from domain_kg.retrieval.models import SearchFilters, SearchRequest
from domain_kg.retrieval.service import RetrievalService
from domain_kg.revisions.models import CurationCommand
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


async def test_source_node_fence_is_immediate_and_survives_inflight_reingestion(
    publishing, monkeypatch
):
    _, editor, queue, coordinator, _, _ = publishing
    sources = []
    for key in ("node-fence-a", "node-fence-b"):
        source, job, checkpoint = await prepare(publishing, key=key)
        await coordinator.publish(editor, await ready(publishing, job, checkpoint))
        sources.append(source)
    alpha = await alpha_id(queue, editor, job.kg_id)
    _, replacement, checkpoint = await prepare(
        publishing,
        command=ReplaceMaterial(
            kg_id=job.kg_id,
            material_id=sources[0].material_id,
            expected_active_version=sources[0].version_id,
            title="Already staged replacement",
            inline_text=TEXT + "\nAn updated source version.",
            idempotency_key="node-fence-reingest",
        ),
    )
    publication = await ready(publishing, replacement, checkpoint)
    service = RevisionService(queue, coordinator.providers)
    command = CurationCommand(
        kg_id=job.kg_id,
        entity_id=alpha,
        action="suppress",
        expected_revision=0,
        source_ids=(sources[0].material_id,),
        reason="Withdraw this node from source A",
        dry_run=True,
        idempotency_key="node-fence-suppress",
    )
    ns = {"org_id": str(editor.org_id), "kg_id": str(job.kg_id)}
    before = await queue.database.curation_revisions.count_documents(ns)
    preview = await service.curate_node(editor, command)
    assert preview.dry_run and preview.material_count == 1 and preview.affected_chunks == 1
    assert await queue.database.curation_revisions.count_documents(ns) == before
    receipt = await service.curate_node(editor, command.model_copy(update={"dry_run": False}))
    assert receipt.state == "applied" and receipt.target_revision == 1 and receipt.job_id is None
    assert (
        await service.curate_node(editor, command.model_copy(update={"dry_run": False})) == receipt
    )
    graph = GraphService(queue)
    node = await graph.get_node(editor, job.kg_id, alpha)
    assert node.observation_count == 1
    assert {item.material_id for item in node.observations} == {sources[1].material_id}
    with pytest.raises(DomainError):
        await graph.get_node(
            editor, job.kg_id, alpha, SearchFilters(material_ids=(sources[0].material_id,))
        )
    search = RetrievalService(queue, coordinator.providers)

    async def check_results():
        for mode in ("vector", "lexical", "graph", "fast", "deep"):
            response = await search.search(
                editor,
                SearchRequest(
                    kg_ids=editor.kg_ids,
                    query="Alpha",
                    mode=mode,
                ),
            )
            assert response.results
            assert all(hit.material_id == sources[1].material_id for hit in response.results)

    await check_results()
    await coordinator.publish(editor, publication)
    await check_results()

    async def forbidden(*args, **kwargs):
        pytest.fail("node restoration checks retained readiness without model calls")

    with monkeypatch.context() as patch:
        patch.setattr(coordinator.providers, "extract", forbidden)
        patch.setattr(coordinator.providers, "embeddings", forbidden)
        restored = await service.curate_node(
            editor,
            command.model_copy(
                update={
                    "action": "restore",
                    "expected_revision": 1,
                    "dry_run": False,
                    "reason": "Restore the checked current projection",
                    "idempotency_key": "node-fence-restore",
                }
            ),
        )
        assert restored.target_revision == 2
    assert (await graph.get_node(editor, job.kg_id, alpha)).observation_count == 2
