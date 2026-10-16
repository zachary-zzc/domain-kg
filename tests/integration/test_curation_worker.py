"""A curation-only worker can publish metadata with no provider or source job claim."""
# ruff: noqa: F811

import pytest
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.graph.service import GraphService
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.materials.models import AddMaterial
from domain_kg.providers.profiles import ProviderRegistry
from domain_kg.providers.service import ProviderService
from domain_kg.retrieval.models import SearchRequest
from domain_kg.revisions.models import NodeChanges, NodeUpdate
from domain_kg.revisions.service import RevisionService
from domain_kg.worker import serve

pytestmark = pytest.mark.storage


async def test_curation_only_worker_publishes_nonembedded_tags_without_a_provider(publishing):
    materials, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    graph = GraphService(queue)
    scope = await graph.resolver.resolve(
        editor, SearchRequest(kg_ids=editor.kg_ids, query="Alpha", mode="graph"), job.kg_id
    )
    alpha = next(
        item.entity.id
        for item in (await graph.repository.open(scope)).observations
        if item.name == "Alpha"
    )
    unclaimed = await materials.add(
        editor,
        AddMaterial(
            kg_id=job.kg_id,
            title="Do not claim this source",
            inline_text="Pending input",
            idempotency_key="curation-worker-pending-source",
        ),
    )
    no_provider = ProviderService(queue.policy, queue.router, ProviderRegistry())
    receipt = await RevisionService(queue, no_provider).update_node(
        editor,
        NodeUpdate(
            kg_id=job.kg_id,
            entity_id=alpha,
            expected_revision=0,
            source_ids=(source.material_id,),
            changes=NodeChanges(tags=("reviewed",)),
            reason="Nonembedded human metadata",
            idempotency_key="curation-worker-tags",
        ),
    )
    await serve(
        queue,
        JobRunner(queue, configured_pipeline(queue, no_provider)),
        once=True,
        worker_id="curation-metadata-only",
        curation_only=True,
    )
    status = await queue.get(editor, receipt.job_id)
    assert status.stage == "ready", status.warnings
    assert (await queue.get(editor, unclaimed.job_id)).stage == "queued"
    node = await graph.get_node(editor, job.kg_id, alpha)
    assert node.name == "Alpha" and node.tags == ("reviewed",)
    assert node.curations[0].changes.name is None
