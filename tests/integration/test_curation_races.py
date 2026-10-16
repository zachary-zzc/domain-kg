"""Competing human edits and revoked actors cannot publish stale or partial fields."""
# ruff: noqa: F811

import asyncio
from datetime import timedelta

import pytest
from test_curation import alpha_id, publish_curation
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.operations import read_operation_artifact
from domain_kg.jobs.queue import now
from domain_kg.jobs.runner import JobRunner
from domain_kg.models import ScopedRef
from domain_kg.revisions.models import NodeChanges, NodeUpdate, PublishRevision, ReadinessReport
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


async def pending(publishing, key="pending", name="Reviewed Alpha"):
    _, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    alpha = await alpha_id(queue, editor, job.kg_id)
    command = NodeUpdate(
        kg_id=job.kg_id,
        entity_id=alpha,
        expected_revision=0,
        source_ids=(source.material_id,),
        changes=NodeChanges(name=name),
        reason="Curation race fixture",
        idempotency_key=key,
    )
    revisions = RevisionService(queue, coordinator.providers)
    receipt = await revisions.update_node(editor, command)
    return source, alpha, command, revisions, receipt


async def staged_command(queue, coordinator, editor, leased):
    pipeline = configured_pipeline(queue, coordinator.providers)
    for step in pipeline.curation_steps[:3]:
        checkpoint = await step.execute(leased)
        await queue.checkpoint(leased, checkpoint)
    owner = ScopedRef(org_id=leased.org_id, kg_id=leased.kg_id, id=leased.target_id)
    report = ReadinessReport.model_validate_json(
        await read_operation_artifact(queue, editor, owner, checkpoint, job=leased)
    )
    lease = await queue.acquire_publication(leased)
    return PublishRevision(
        report=report,
        lease_token=lease.lease_token,
        fence=lease.fence,
        expected_revision=report.batch.expected_revision,
        idempotency_key=f"publish-curation-{leased.id}",
    )


async def test_competing_same_scope_edits_cannot_overwrite_the_winner(publishing, monkeypatch):
    _, editor, queue, coordinator, _, _ = publishing
    source, alpha, command, revisions, first = await pending(publishing, name="Winner")
    second = await revisions.update_node(
        editor,
        command.model_copy(
            update={"idempotency_key": "competing", "changes": NodeChanges(name="Loser")}
        ),
    )
    await publish_curation(queue, coordinator, editor, first)

    async def forbidden(*args, **kwargs):
        pytest.fail("a stale curation must fail before any provider call")

    monkeypatch.setattr(coordinator.providers, "embeddings", forbidden)
    leased = await queue.claim("stale-curation")
    assert leased.id == second.job_id
    await JobRunner(queue, configured_pipeline(queue, coordinator.providers)).run_job(leased)
    status = await queue.get(editor, second.job_id)
    assert status.stage == "failed" and "VERSION_CONFLICT" in status.warnings
    assert (await GraphService(queue).get_node(editor, command.kg_id, alpha)).name == "Winner"


async def test_current_grant_revocation_blocks_prepared_curation(publishing, team):
    _, editor, queue, coordinator, _, _ = publishing
    source, alpha, command, _, receipt = await pending(publishing)
    leased = await queue.claim("revoked-curation")
    publication = await staged_command(queue, coordinator, editor, leased)
    owner = await queue.policy.resolve_access(
        team[2]["owner"], [command.kg_id], Action.MANAGE_GRANTS
    )
    await queue.policy.set_material_acl(
        owner,
        command.kg_id,
        source.material_id,
        "restricted",
        member_ids=(team[3]["owner"].id,),
        group_ids=(),
        idempotency_key="curation-revoke",
    )
    with pytest.raises(DomainError):
        await coordinator.publish(editor, publication)
    with pytest.raises(DomainError):
        await queue.get(editor, receipt.job_id)
    owner = await queue.policy.resolve_access(team[2]["owner"], [command.kg_id], Action.SEARCH)
    assert (await GraphService(queue).get_node(owner, command.kg_id, alpha)).name == "Alpha"
    assert (await queue.database.kgs.find_one({"id": str(command.kg_id)}))[
        "published_revision"
    ] == 1


async def test_identical_concurrent_publication_replays_one_accepted_receipt(
    publishing, monkeypatch
):
    _, editor, queue, coordinator, _, _ = publishing
    _, _, command, _, receipt = await pending(publishing)
    leased = await queue.claim("curation-ack-race")
    publication = await staged_command(queue, coordinator, editor, leased)
    transaction = queue.transactions.transaction
    reached = 0
    together = asyncio.Event()

    async def barrier(work):
        nonlocal reached
        if work.__name__ == "accept":
            reached += 1
            if reached == 2:
                together.set()
            await asyncio.wait_for(together.wait(), 10)
        return await transaction(work)

    monkeypatch.setattr(queue.transactions, "transaction", barrier)
    results = await asyncio.gather(
        coordinator.publish(editor, publication),
        coordinator.publish(editor, publication),
        return_exceptions=True,
    )
    assert not any(isinstance(item, Exception) for item in results), results
    assert results[0] == results[1] and results[0].revision_id == receipt.revision_id
    assert (await queue.database.kgs.find_one({"id": str(command.kg_id)}))[
        "published_revision"
    ] == 2


async def test_projection_failure_keeps_old_fields_and_retry_reuses_sealed_vectors(
    publishing, monkeypatch
):
    from domain_kg.storage.qdrant import VectorProjection

    _, editor, queue, coordinator, _, faults = publishing
    _, alpha, command, _, receipt = await pending(publishing)
    leased = await queue.claim("partial-curation")
    runner = JobRunner(queue, configured_pipeline(queue, coordinator.providers))

    async def offline(*args, **kwargs):
        raise DomainError(ErrorCode.BACKEND_UNAVAILABLE, "Fixture vector outage", retryable=True)

    with monkeypatch.context() as patch:
        patch.setattr(VectorProjection, "write", offline)
        await runner.run_job(leased)
    assert (await queue.get(editor, receipt.job_id)).stage == "queued"
    assert (await GraphService(queue).get_node(editor, command.kg_id, alpha)).name == "Alpha"
    await queue.database.jobs.update_one(
        {"id": str(receipt.job_id)}, {"$set": {"available_at": now() - timedelta(seconds=1)}}
    )
    successor = await queue.claim("recovered-curation")
    assert successor.id == leased.id and successor.attempt_id != leased.attempt_id

    async def forbidden(*args, **kwargs):
        pytest.fail("recovery must reuse the sealed manual embedding artifact")

    monkeypatch.setattr(coordinator.providers, "embeddings", forbidden)
    faults["embedding_inputs"].clear()
    await runner.run_job(successor)
    status = await queue.get(editor, receipt.job_id)
    assert status.stage == "ready", status.warnings
    assert faults["embedding_inputs"] == []
    assert (
        await GraphService(queue).get_node(editor, command.kg_id, alpha)
    ).name == "Reviewed Alpha"


async def test_accepted_publication_replay_still_requires_write_scope(publishing):
    _, editor, queue, coordinator, _, _ = publishing
    _, _, command, _, _ = await pending(publishing)
    leased = await queue.claim("curation-replay-scope")
    publication = await staged_command(queue, coordinator, editor, leased)
    await coordinator.publish(editor, publication)
    read_only = editor.principal.model_copy(update={"scopes": frozenset({"domain_kg:read"})})
    current = await queue.policy.resolve_access(read_only, [command.kg_id], Action.READ)
    with pytest.raises(DomainError):
        await coordinator.publish(current, publication)
