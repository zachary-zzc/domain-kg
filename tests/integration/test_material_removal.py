"""A Mongo visibility fence withdraws only the removed material's ownership."""
# ruff: noqa: F811

import pytest
from test_entity_resolution import TEXT
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.materials.lifecycle import MaterialLifecycle
from domain_kg.materials.models import RemoveMaterial, ReplaceMaterial
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
