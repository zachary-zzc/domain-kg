"""A Mongo visibility fence withdraws only the removed material's ownership."""
# ruff: noqa: F811

import pytest
from test_entity_resolution import TEXT
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.materials.lifecycle import MaterialLifecycle
from domain_kg.materials.models import RemoveMaterial, ReplaceMaterial, RestoreMaterial
from domain_kg.retrieval.models import SearchFilters, SearchRequest
from domain_kg.retrieval.service import RetrievalService

pytestmark = pytest.mark.storage


async def test_removal_fence_keeps_independent_support_and_rejects_late_publisher(publishing, team):
    materials, editor, queue, coordinator, _, _ = publishing
    receipts = []
    for key in ("source-a", "source-b"):
        receipt, job, checkpoint = await prepare(publishing, key=key)
        await coordinator.publish(editor, await ready(publishing, job, checkpoint))
        receipts.append(receipt)
    first, second = receipts
    reader = await queue.policy.resolve_access(
        team[2]["reader"], list(editor.kg_ids), Action.SEARCH
    )
    graph = GraphService(queue)
    request = SearchRequest(kg_ids=reader.kg_ids, query="Alpha", mode="deep")
    old_scope = await graph.resolver.resolve(reader, request, job.kg_id)
    data = await graph.repository.open(old_scope)
    alpha = next(item.entity.id for item in data.observations if item.name == "Alpha")
    assert (await graph.get_node(reader, job.kg_id, alpha)).support_count == 2
    command = ReplaceMaterial(
        kg_id=job.kg_id,
        material_id=first.material_id,
        expected_active_version=first.version_id,
        title="late replacement",
        inline_text=TEXT + "\nLate preparation.",
        idempotency_key="late",
    )
    _, late_job, checkpoint = await prepare(publishing, command=command)
    late_publication = await ready(publishing, late_job, checkpoint)
    lifecycle = MaterialLifecycle(materials, queue)
    row = await queue.database.material_access.find_one(
        {"org_id": str(editor.org_id), "kg_id": str(job.kg_id), "id": str(first.material_id)}
    )
    remove = RemoveMaterial(
        kg_id=job.kg_id,
        material_id=first.material_id,
        action="delete",
        expected_revision=row["revision"],
        idempotency_key="delete-a",
    )
    dry = await lifecycle.remove(editor, remove.model_copy(update={"dry_run": True}))
    assert dry.dry_run and dry.revision == row["revision"]
    assert dry.impact.versions == 2 and dry.impact.pending_jobs == 1
    assert dry.impact.original_bytes == len(TEXT.encode()) + len(
        (TEXT + "\nLate preparation.").encode()
    )
    assert await queue.policy.material_visible(reader, job.kg_id, first.material_id)
    removed = await lifecycle.remove(editor, remove)
    assert removed.revision == row["revision"] + 1 and not removed.dry_run
    assert await lifecycle.remove(editor, remove) == removed
    with pytest.raises(DomainError):
        await graph.resolver.revalidate(old_scope)
    with pytest.raises(DomainError):
        await coordinator.publish(editor, late_publication)
    node = await graph.get_node(reader, job.kg_id, alpha)
    assert node.support_count == 1
    assert {item.material_id for item in node.observations} == {second.material_id}
    result = await RetrievalService(queue, coordinator.providers).search(reader, request)
    assert {item.material_id for item in result.results} == {second.material_id}
    empty = await RetrievalService(queue, coordinator.providers).search(
        reader,
        request.model_copy(
            update={
                "filters": SearchFilters(
                    material_ids=(first.material_id,), version_id=first.version_id
                )
            }
        ),
    )
    assert empty.results == ()
    with pytest.raises(DomainError):
        _ = b"".join(
            [
                part
                async for part in materials.original(
                    reader, job.kg_id, first.material_id, first.version_id
                )
            ]
        )


async def test_removal_requires_current_material_permission_and_revision(publishing, team):
    materials, editor, queue, coordinator, _, _ = publishing
    receipt, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    lifecycle = MaterialLifecycle(materials, queue)
    command = RemoveMaterial(
        kg_id=job.kg_id,
        material_id=receipt.material_id,
        action="exclude",
        expected_revision=0,
        idempotency_key="exclude",
    )
    with pytest.raises(DomainError) as error:
        await lifecycle.remove(editor, command)
    assert error.value.code == ErrorCode.VERSION_CONFLICT
    reader = await queue.policy.resolve_access(team[2]["reader"], [job.kg_id], Action.READ)
    with pytest.raises(DomainError):
        await lifecycle.remove(reader, command.model_copy(update={"expected_revision": 1}))


async def test_restore_checks_retained_projections_before_clearing_visibility(
    publishing, team, monkeypatch
):
    import domain_kg.revisions.publication as publication

    materials, editor, queue, coordinator, _, _ = publishing
    receipt, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    lifecycle = MaterialLifecycle(materials, queue)
    removed = await lifecycle.remove(
        editor,
        RemoveMaterial(
            kg_id=job.kg_id,
            material_id=receipt.material_id,
            action="delete",
            expected_revision=1,
            idempotency_key="remove-before-restore",
        ),
    )
    reader = await queue.policy.resolve_access(team[2]["reader"], [job.kg_id], Action.READ)
    original = publication.verify_document
    observations = []

    async def still_hidden(*args, **kwargs):
        assert not await queue.policy.material_visible(reader, job.kg_id, receipt.material_id)
        observations.append(True)
        return await original(*args, **kwargs)

    monkeypatch.setattr(publication, "verify_document", still_hidden)
    command = RestoreMaterial(
        kg_id=job.kg_id,
        material_id=receipt.material_id,
        expected_revision=removed.revision,
        idempotency_key="restore-ready",
    )
    restored = await lifecycle.restore(editor, command)
    assert restored.state == "restored" and restored.revision == removed.revision + 1
    assert observations and await lifecycle.restore(editor, command) == restored
    assert await queue.policy.material_visible(reader, job.kg_id, receipt.material_id)
    original_bytes = b"".join(
        [
            part
            async for part in materials.original(
                reader, job.kg_id, receipt.material_id, receipt.version_id
            )
        ]
    )
    assert original_bytes.decode() == TEXT


async def test_restore_failure_preserves_fence_and_requires_current_acl(
    publishing, team, monkeypatch
):
    import domain_kg.revisions.publication as publication

    materials, editor, queue, coordinator, _, _ = publishing
    receipt, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    lifecycle = MaterialLifecycle(materials, queue)
    removed = await lifecycle.remove(
        editor,
        RemoveMaterial(
            kg_id=job.kg_id,
            material_id=receipt.material_id,
            action="exclude",
            expected_revision=1,
            idempotency_key="exclude-before-restore",
        ),
    )
    command = RestoreMaterial(
        kg_id=job.kg_id,
        material_id=receipt.material_id,
        expected_revision=removed.revision,
        idempotency_key="restore-failure",
    )

    async def broken(*args, **kwargs):
        raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Retained projection is missing.")

    with monkeypatch.context() as patch:
        patch.setattr(publication, "verify_document", broken)
        pending = await lifecycle.restore(editor, command)
        assert pending.state == "pending" and pending.job_id is not None
    assert not await queue.policy.material_visible(editor, job.kg_id, receipt.material_id)
    # Revoking the current KG grant also revokes access to a previously accepted restore intent.
    owner = await queue.policy.resolve_access(team[2]["owner"], [job.kg_id], Action.MANAGE_GRANTS)
    await queue.policy.set_grant(
        owner,
        job.kg_id,
        "member",
        team[3]["editor"].id,
        None,
        idempotency_key="revoke-before-restore",
    )
    with pytest.raises(DomainError):
        await lifecycle.restore(editor, command)


async def test_concurrent_restore_replays_the_winning_receipt(publishing, monkeypatch):
    import asyncio

    import domain_kg.revisions.publication as publication

    materials, editor, queue, coordinator, _, _ = publishing
    receipt, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    lifecycle = MaterialLifecycle(materials, queue)
    removed = await lifecycle.remove(
        editor,
        RemoveMaterial(
            kg_id=job.kg_id,
            material_id=receipt.material_id,
            action="delete",
            expected_revision=1,
            idempotency_key="remove-for-concurrent-restore",
        ),
    )
    command = RestoreMaterial(
        kg_id=job.kg_id,
        material_id=receipt.material_id,
        expected_revision=removed.revision,
        idempotency_key="same-restore",
    )
    original, both, finished, calls = (
        publication.verify_document,
        asyncio.Event(),
        asyncio.Event(),
        [],
    )

    async def staggered(*args, **kwargs):
        value = await original(*args, **kwargs)
        calls.append(True)
        async with asyncio.timeout(10):
            if len(calls) == 1:
                await both.wait()
            else:
                both.set()
                await finished.wait()
        return value

    monkeypatch.setattr(publication, "verify_document", staggered)

    async def restore_once():
        try:
            return await lifecycle.restore(editor, command)
        finally:
            finished.set()

    first, second = await asyncio.gather(restore_once(), restore_once())
    assert first == second and first.state == "restored"


async def test_exclusion_is_reversible_while_deleted_data_obeys_retention(publishing):
    from datetime import UTC, datetime, timedelta

    materials, editor, queue, coordinator, _, _ = publishing
    receipt, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    lifecycle = MaterialLifecycle(materials, queue)
    excluded = await lifecycle.remove(
        editor,
        RemoveMaterial(
            kg_id=job.kg_id,
            material_id=receipt.material_id,
            action="exclude",
            expected_revision=1,
            idempotency_key="reversible-exclusion",
        ),
    )
    target = {"org_id": str(job.org_id), "kg_id": str(job.kg_id), "id": str(receipt.material_id)}
    await queue.database.material_access.update_one(
        target, {"$set": {"purge_after": datetime.now(UTC) - timedelta(days=1)}}
    )
    restored = await lifecycle.restore(
        editor,
        RestoreMaterial(
            kg_id=job.kg_id,
            material_id=receipt.material_id,
            expected_revision=excluded.revision,
            idempotency_key="restore-old-exclusion",
        ),
    )
    assert restored.state == "restored"
    deleted = await lifecycle.remove(
        editor,
        RemoveMaterial(
            kg_id=job.kg_id,
            material_id=receipt.material_id,
            action="delete",
            expected_revision=restored.revision,
            idempotency_key="retained-deletion",
        ),
    )
    await queue.database.material_access.update_one(
        target, {"$set": {"purge_after": datetime.now(UTC) - timedelta(days=1)}}
    )
    with pytest.raises(DomainError) as error:
        await lifecycle.restore(
            editor,
            RestoreMaterial(
                kg_id=job.kg_id,
                material_id=receipt.material_id,
                expected_revision=deleted.revision,
                idempotency_key="restore-expired-deletion",
            ),
        )
    assert error.value.code == ErrorCode.VERSION_CONFLICT
