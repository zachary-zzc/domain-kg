"""Shared target fences reject conflicting human edits before model work."""
# ruff: noqa: F811

import asyncio

import pytest
from test_curation import alpha_id
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError
from domain_kg.graph.service import GraphService
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.revisions.models import CurationCommand, NodeChanges, NodeUpdate
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


async def source_node(publishing):
    _, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    alpha = await alpha_id(queue, editor, job.kg_id)
    command = CurationCommand(
        kg_id=job.kg_id,
        entity_id=alpha,
        action="suppress",
        expected_revision=0,
        source_ids=(source.material_id,),
        reason="Concurrent fence",
        idempotency_key="fence-writer-a",
    )
    return source, alpha, RevisionService(queue, coordinator.providers), command


async def test_concurrent_same_scope_fences_have_one_winner(publishing):
    _, editor, queue, _, _, _ = publishing
    _, _, service, command = await source_node(publishing)
    outcomes = await asyncio.gather(
        service.curate_node(editor, command),
        service.curate_node(
            editor, command.model_copy(update={"idempotency_key": "fence-writer-b"})
        ),
        return_exceptions=True,
    )
    assert sum(not isinstance(result, BaseException) for result in outcomes) == 1
    assert sum(isinstance(result, DomainError) for result in outcomes) == 1
    ns = {"org_id": str(editor.org_id), "kg_id": str(command.kg_id)}
    assert (await queue.database.curation_targets.find_one(ns))["revision"] == 1
    assert await queue.database.node_action_commands.count_documents(ns) == 1
    assert (
        await queue.database.outbox.count_documents(ns | {"kind": "node_visibility_changed"}) == 1
    )


async def test_suppression_fences_a_pending_node_edit_before_embedding(publishing, monkeypatch):
    _, editor, queue, coordinator, _, _ = publishing
    source, alpha, service, command = await source_node(publishing)
    pending = await service.update_node(
        editor,
        NodeUpdate(
            kg_id=command.kg_id,
            entity_id=alpha,
            expected_revision=0,
            source_ids=(source.material_id,),
            changes=NodeChanges(name="Stale pending name"),
            reason="Pending field edit",
            idempotency_key="node-edit-before-fence",
        ),
    )
    await service.curate_node(editor, command)

    async def forbidden(*args, **kwargs):
        pytest.fail("stale node edits and visibility restoration require no model work")

    with monkeypatch.context() as patch:
        patch.setattr(coordinator.providers, "extract", forbidden)
        patch.setattr(coordinator.providers, "embeddings", forbidden)
        leased = await queue.claim("stale-node-fields")
        if leased is not None:
            assert leased.id == pending.job_id
            await JobRunner(queue, configured_pipeline(queue, coordinator.providers)).run_job(
                leased
            )
        status = await queue.get(editor, pending.job_id)
        assert status.stage == "failed" and "VERSION_CONFLICT" in status.warnings
        await service.curate_node(
            editor,
            command.model_copy(
                update={
                    "action": "restore",
                    "expected_revision": 1,
                    "idempotency_key": "restore-after-stale-edit",
                }
            ),
        )
    assert (await GraphService(queue).get_node(editor, command.kg_id, alpha)).name == "Alpha"
