"""Restore a never-published original without exposing intermediate construction."""
# ruff: noqa: F811

import pytest
from test_entity_resolution import TEXT
from test_publication import publishing  # noqa: F401

from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.materials.lifecycle import MaterialLifecycle
from domain_kg.materials.models import AddMaterial, RemoveMaterial, RestoreMaterial
from domain_kg.retrieval.models import SearchRequest
from domain_kg.retrieval.service import RetrievalService

pytestmark = pytest.mark.storage


async def test_removed_unpublished_source_reprocesses_from_retained_original(publishing):
    materials, editor, queue, coordinator, _, _ = publishing
    kg = editor.kg_ids[0]
    source = await materials.add(
        editor,
        AddMaterial(
            kg_id=kg,
            title="Removed before first publication",
            inline_text=TEXT,
            idempotency_key="unpublished-restore-source",
        ),
    )
    lifecycle = MaterialLifecycle(materials, queue)
    removed = await lifecycle.remove(
        editor,
        RemoveMaterial(
            kg_id=kg,
            material_id=source.material_id,
            action="delete",
            expected_revision=0,
            idempotency_key="unpublished-source-removed",
        ),
    )
    command = RestoreMaterial(
        kg_id=kg,
        material_id=source.material_id,
        expected_revision=removed.revision,
        idempotency_key="unpublished-source-restore",
    )
    receipt = await lifecycle.restore(editor, command)
    assert receipt.state == "pending" and receipt.job_id
    assert await lifecycle.restore(editor, command) == receipt
    assert not await queue.policy.material_visible(editor, kg, source.material_id)
    search = RetrievalService(queue, coordinator.providers)
    assert (await search.search(editor, SearchRequest(kg_ids=(kg,), query="Alpha"))).results == ()
    job = await queue.claim("unpublished-restorer", kinds=("reprocess_material",))
    assert job and job.id == receipt.job_id
    await JobRunner(queue, configured_pipeline(queue, coordinator.providers)).run_job(job)
    assert (await queue.get(editor, job.id)).stage == "ready"
    restored = await lifecycle.restore(editor, command)
    assert restored.state == "restored" and restored.revision == removed.revision + 1
    assert await queue.policy.material_visible(editor, kg, source.material_id)
    original = b"".join(
        [
            part
            async for part in materials.original(
                editor,
                kg,
                source.material_id,
                source.version_id,
            )
        ]
    )
    assert original.decode() == TEXT
    for mode in ("vector", "lexical", "graph", "fast", "deep"):
        result = await search.search(editor, SearchRequest(kg_ids=(kg,), query="Alpha", mode=mode))
        assert result.results and all(
            hit.material_id == source.material_id for hit in result.results
        )
