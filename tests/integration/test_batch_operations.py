"""Batch source controls preserve shared KG facts, versions and per-item authority."""

import asyncio
from uuid import uuid4

import pytest
from test_publication import make_publishing, prepare, ready

from domain_kg.catalog.models import CreateKG
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.materials.lifecycle import MaterialLifecycle
from domain_kg.materials.models import AddMaterial
from domain_kg.operations.batch import BatchCommand, BatchOperations, BatchTarget
from domain_kg.retrieval.models import SearchRequest
from domain_kg.revisions.journal import PublicationJournal

pytestmark = pytest.mark.storage


@pytest.fixture
async def batch_workspace(material_service, team):
    return await make_publishing(material_service, team)


async def test_two_sources_publish_one_graph_and_batch_delete_preserves_other_support(
    batch_workspace, team
):
    publishing = batch_workspace
    materials, access, queue, coordinator, _, _ = publishing
    receipts = []
    for key in ("shared-a", "shared-b"):
        receipt, job, checkpoint = await prepare(publishing, key=key)
        await coordinator.publish(access, await ready(publishing, job, checkpoint))
        receipts.append(receipt)
    kg = access.kg_ids[0]
    view = await PublicationJournal(queue).load(access, kg)
    assert {s.material_id for s in view.sources} == {r.material_id for r in receipts}
    graph = GraphService(queue)
    scope = await graph.resolver.resolve(access, SearchRequest(kg_ids=(kg,), query="Alpha"), kg)
    data = await graph.repository.open(scope)
    alpha_ids = {o.entity.id for o in data.observations if o.name == "Alpha"}
    assert len(alpha_ids) == 1
    alpha = next(iter(alpha_ids))
    assert (await graph.get_node(access, kg, alpha)).support_count == 2
    assert len({row.claim.assertion.id for row in data.rows if row.claim}) == 1
    batch = BatchOperations(queue, MaterialLifecycle(materials, queue))
    command = BatchCommand(
        operation="delete",
        idempotency_key="bulk-delete-shared",
        items=(
            BatchTarget(id=receipts[0].material_id, revision=1),
            BatchTarget(id=uuid4(), revision=0),
            BatchTarget(id=receipts[1].material_id, revision=0),
        ),
    )
    result = await batch.execute(access, kg, command)
    assert [r.outcome for r in result.items] == ["completed", "failed", "failed"]
    node = await graph.get_node(access, kg, alpha)
    assert node.support_count == 1
    assert {o.material_id for o in node.observations} == {receipts[1].material_id}
    assert await batch.execute(access, kg, command) == result
    restored = await batch.execute(
        access,
        kg,
        BatchCommand(
            operation="restore",
            idempotency_key="bulk-restore",
            items=(BatchTarget(id=receipts[0].material_id, revision=2),),
        ),
    )
    assert restored.items[0].outcome == "completed"
    assert (await graph.get_node(access, kg, alpha)).support_count == 2
    owner = await queue.policy.resolve_access(team[2]["owner"], [kg], Action.MANAGE_GRANTS)
    await queue.policy.set_material_acl(
        owner,
        kg,
        receipts[0].material_id,
        "restricted",
        member_ids=(),
        group_ids=(),
        idempotency_key="hide-bulk-result",
    )
    fresh = await queue.policy.resolve_access(access.principal, [kg], Action.READ)
    hidden = await batch.get(fresh, kg, result.batch_id)
    assert hidden.items[0].outcome == "failed" and hidden.items[0].job_id is None


async def test_batch_reprocess_preserves_original_and_old_view_until_verified(batch_workspace):
    publishing = batch_workspace
    materials, access, queue, coordinator, steps, _ = publishing
    receipt, job, checkpoint = await prepare(publishing)
    await coordinator.publish(access, await ready(publishing, job, checkpoint))
    kg = access.kg_ids[0]
    batch = BatchOperations(queue, MaterialLifecycle(materials, queue))
    command = BatchCommand(
        operation="process",
        idempotency_key="bulk-reprocess",
        items=(BatchTarget(id=receipt.material_id, revision=1, version_id=receipt.version_id),),
    )
    result = await batch.execute(access, kg, command)
    assert result.items[0].outcome == "submitted"
    before = await PublicationJournal(queue).load(access, kg)
    assert before.sources[0].version_id == receipt.version_id
    assert await batch.execute(access, kg, command) == result
    overlapping = await batch.execute(
        access, kg, command.model_copy(update={"idempotency_key": "another-reprocess-batch"})
    )
    assert overlapping.items[0].outcome == "skipped"
    assert overlapping.items[0].job_id == result.items[0].job_id
    replacement = await queue.claim("bulk-reprocessor")
    assert replacement is not None and replacement.id == result.items[0].job_id
    assert replacement.kind == "replace" and replacement.target_id != receipt.version_id
    for step in steps:
        checkpoint = await step.execute(replacement)
        await queue.checkpoint(replacement, checkpoint)
    await coordinator.publish(access, await ready(publishing, replacement, checkpoint))
    after = await PublicationJournal(queue).load(access, kg)
    assert len(after.sources) == 1 and after.sources[0].material_id == receipt.material_id
    assert after.sources[0].version_id == replacement.target_id
    old = b"".join(
        [p async for p in materials.original(access, kg, receipt.material_id, receipt.version_id)]
    )
    new = b"".join(
        [
            p
            async for p in materials.original(
                access, kg, receipt.material_id, replacement.target_id
            )
        ]
    )
    assert new == old


async def test_batch_pending_process_concurrent_replay_and_cross_kg(batch_workspace, catalog, team):
    publishing = batch_workspace
    materials, access, queue, _, _, _ = publishing
    kg = access.kg_ids[0]
    pending = await materials.add(
        access,
        AddMaterial(
            kg_id=kg,
            title="Pending bulk",
            inline_text="Pending synthetic source.",
            idempotency_key="pending-bulk",
        ),
    )
    await queue.cancel(access, pending.job_id, idempotency_key="stop-pending-bulk")
    batch = BatchOperations(queue, MaterialLifecycle(materials, queue))
    command = BatchCommand(
        operation="process",
        idempotency_key="start-pending-bulk",
        items=(BatchTarget(id=pending.material_id, revision=0),),
    )
    await asyncio.gather(batch.execute(access, kg, command), batch.execute(access, kg, command))
    identifier = (await batch.execute(access, kg, command)).batch_id
    assert (await batch.get(access, kg, identifier)).items[0].outcome == "submitted"
    assert (await queue.get(access, pending.job_id)).stage == "queued"
    assert (
        await queue.database.job_commands.count_documents(
            {"job_id": str(pending.job_id), "operation": "retry"}
        )
        == 1
    )
    owner = await queue.policy.resolve_access(team[2]["owner"], [], Action.CREATE_KG)
    other = await catalog[0].create_kg(
        owner,
        CreateKG(
            name="Other bulk KG",
            domain_profile_id=catalog[0].general_profile_id,
            placement_id=catalog[3].id,
            idempotency_key="other-bulk-kg",
        ),
    )
    multi = await queue.policy.resolve_access(team[2]["owner"], [kg, other.kg_id], Action.INGEST)
    foreign = await materials.add(
        multi,
        AddMaterial(
            kg_id=other.kg_id,
            title="Foreign batch target",
            inline_text="Foreign synthetic source.",
            idempotency_key="foreign-bulk",
        ),
    )
    rejected = await batch.execute(
        multi,
        kg,
        BatchCommand(
            operation="cancel",
            idempotency_key="foreign-bulk-cancel",
            items=(BatchTarget(id=foreign.job_id),),
        ),
    )
    assert rejected.items[0].outcome == "failed"
    assert (await queue.get(multi, foreign.job_id)).stage == "queued"


async def test_interrupted_batch_resumes_only_unsubmitted_items(batch_workspace, monkeypatch):
    materials, access, queue, _, _, _ = batch_workspace
    kg = access.kg_ids[0]
    ids = []
    for i in range(2):
        receipt = await materials.add(
            access,
            AddMaterial(
                kg_id=kg,
                title=f"Resume {i}",
                inline_text="Synthetic resume source.",
                idempotency_key=f"resume-batch-{i}",
            ),
        )
        await queue.cancel(access, receipt.job_id, idempotency_key=f"resume-stop-{i}")
        ids.append(receipt.job_id)
    batch = BatchOperations(queue, MaterialLifecycle(materials, queue))
    command = BatchCommand(
        operation="retry",
        idempotency_key="resume-batch",
        items=tuple(BatchTarget(id=i) for i in ids),
    )
    original = batch._one
    calls = []

    async def interrupt(*args, **kwargs):
        calls.append(args[3].id)
        if len(calls) == 2:
            raise asyncio.CancelledError()
        return await original(*args, **kwargs)

    monkeypatch.setattr(batch, "_one", interrupt)
    with pytest.raises(asyncio.CancelledError):
        await batch.execute(access, kg, command)
    monkeypatch.setattr(batch, "_one", original)
    result = await batch.execute(access, kg, command)
    assert [item.outcome for item in result.items] == ["submitted", "submitted"]
    assert (
        await queue.database.job_commands.count_documents(
            {"job_id": {"$in": [str(i) for i in ids]}, "operation": "retry"}
        )
        == 2
    )
