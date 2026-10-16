"""Human fences keep role, current-grant, readiness and authoritative scope proofs."""
# ruff: noqa: F811

from uuid import uuid4

import pytest
from test_curation import alpha_id, publish_curation
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError
from domain_kg.graph.node_actions import NodeVisibilityService
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.retrieval.models import SearchFilters, SearchRequest
from domain_kg.retrieval.service import RetrievalService
from domain_kg.revisions.models import CurationCommand, NodeChanges, NodeUpdate
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


async def fenced(publishing):
    _, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    alpha = await alpha_id(queue, editor, job.kg_id)
    service = RevisionService(queue, coordinator.providers)
    command = CurationCommand(
        kg_id=job.kg_id,
        entity_id=alpha,
        action="suppress",
        expected_revision=0,
        source_ids=(source.material_id,),
        reason="Hold this source node",
        idempotency_key="policy-node-suppress",
    )
    await service.curate_node(editor, command)
    return source, alpha, service, command


async def test_public_fence_requires_owner_and_covers_public_human_fields(
    publishing, team, monkeypatch
):
    _, editor, queue, coordinator, _, _ = publishing
    _, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    alpha = await alpha_id(queue, editor, job.kg_id)
    service = RevisionService(queue, coordinator.providers)
    owner = await queue.policy.resolve_access(team[2]["owner"], [job.kg_id], Action.CURATE)
    reader = await queue.policy.resolve_access(team[2]["reader"], [job.kg_id], Action.READ)
    public = await service.update_node(
        owner,
        NodeUpdate(
            kg_id=job.kg_id,
            entity_id=alpha,
            expected_revision=0,
            curation_scope="kg_public",
            changes=NodeChanges(name="Public human label"),
            reason="Explicit public field",
            idempotency_key="public-node-label",
        ),
    )
    await publish_curation(queue, coordinator, owner, public)
    command = CurationCommand(
        kg_id=job.kg_id,
        entity_id=alpha,
        action="suppress",
        expected_revision=1,
        curation_scope="kg_public",
        reason="Withdraw node across the KG",
        idempotency_key="public-node-fence",
    )
    for denied in (reader, editor):
        with pytest.raises(DomainError):
            await service.curate_node(denied, command)
    receipt = await service.curate_node(owner, command)
    assert receipt.target_revision == 2

    async def forbidden(*args, **kwargs):
        pytest.fail("empty eligible recall and retained restore require no model")

    with monkeypatch.context() as patch:
        patch.setattr(coordinator.providers, "extract", forbidden)
        patch.setattr(coordinator.providers, "embeddings", forbidden)
        for mode in ("vector", "lexical", "graph", "fast", "deep"):
            response = await RetrievalService(queue, coordinator.providers).search(
                reader,
                SearchRequest(
                    kg_ids=(job.kg_id,),
                    query="Alpha",
                    mode=mode,
                ),
            )
            assert response.results == ()
        with pytest.raises(DomainError):
            await GraphService(queue).get_node(reader, job.kg_id, alpha)
        await service.curate_node(
            owner,
            command.model_copy(
                update={
                    "action": "restore",
                    "expected_revision": 2,
                    "idempotency_key": "public-node-restore",
                }
            ),
        )
    assert (
        await GraphService(queue).get_node(reader, job.kg_id, alpha)
    ).name == "Public human label"


async def test_restore_keeps_fence_when_a_projection_is_corrupt(publishing):
    _, editor, queue, _, _, _ = publishing
    _, _, service, command = await fenced(publishing)
    graph = await queue.router.for_graph(editor, command.kg_id)
    await graph.driver.execute_query(
        "MATCH (n:ScopedProjection {org_id:$org_id,kg_id:$kg_id,kind:'chunk'}) "
        "SET n.body_hash=$bad",
        org_id=str(editor.org_id),
        kg_id=str(command.kg_id),
        bad="f" * 64,
    )
    with pytest.raises(DomainError):
        await service.curate_node(
            editor,
            command.model_copy(
                update={
                    "action": "restore",
                    "expected_revision": 1,
                    "idempotency_key": "corrupt-node-restore",
                }
            ),
        )
    ns = {"org_id": str(editor.org_id), "kg_id": str(command.kg_id)}
    assert (await queue.database.node_visibility.find_one(ns))["suppressed"] is True
    assert (await queue.database.curation_targets.find_one(ns))["revision"] == 1
    assert await queue.database.node_action_commands.count_documents(ns) == 1


async def test_revocation_after_readiness_cannot_clear_the_fence(publishing, team, monkeypatch):
    _, editor, queue, _, _, _ = publishing
    source, _, service, command = await fenced(publishing)
    original = NodeVisibilityService._readiness

    async def revoke(instance, scope):
        await original(instance, scope)
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
            idempotency_key="revoke-node-restore",
        )

    monkeypatch.setattr(NodeVisibilityService, "_readiness", revoke)
    with pytest.raises(DomainError):
        await service.curate_node(
            editor,
            command.model_copy(
                update={
                    "action": "restore",
                    "expected_revision": 1,
                    "idempotency_key": "revoked-node-restore",
                }
            ),
        )
    ns = {"org_id": str(editor.org_id), "kg_id": str(command.kg_id)}
    assert (await queue.database.node_visibility.find_one(ns))["suppressed"] is True
    with pytest.raises(DomainError):
        await service.curate_node(editor, command)


async def test_changed_or_missing_fence_mirror_cannot_unhide_source_filtered_results(publishing):
    _, editor, queue, _, _, _ = publishing
    source, alpha, _, command = await fenced(publishing)
    ns = {"org_id": str(editor.org_id), "kg_id": str(command.kg_id)}
    await queue.database.node_visibility.update_one(ns, {"$set": {"material_ids": [str(uuid4())]}})
    with pytest.raises(DomainError):
        await GraphService(queue).get_node(
            editor, command.kg_id, alpha, SearchFilters(material_ids=(source.material_id,))
        )

    await queue.database.node_visibility.delete_many(ns)
    with pytest.raises(DomainError):
        await GraphService(queue).get_node(
            editor, command.kg_id, alpha, SearchFilters(material_ids=(source.material_id,))
        )


async def test_replay_rejects_a_receipt_with_a_false_action_or_revision(publishing):
    _, editor, queue, _, _, _ = publishing
    _, _, service, command = await fenced(publishing)
    ns = {"org_id": str(editor.org_id), "kg_id": str(command.kg_id)}
    await queue.database.node_action_commands.update_one(
        ns,
        {
            "$set": {
                "receipt.action": "restore",
                "receipt.target_revision": 999,
            }
        },
    )
    with pytest.raises(DomainError):
        await service.curate_node(editor, command)


async def test_restore_rejects_an_unrelated_target_state_record(publishing):
    _, editor, queue, _, _, _ = publishing
    _, _, service, command = await fenced(publishing)
    ns = {"org_id": str(editor.org_id), "kg_id": str(command.kg_id)}
    await queue.database.curation_targets.update_one(
        ns,
        {
            "$set": {
                "kind": "relation",
                "target_id": str(uuid4()),
            }
        },
    )
    with pytest.raises(DomainError):
        await service.curate_node(
            editor,
            command.model_copy(
                update={
                    "action": "restore",
                    "expected_revision": 1,
                    "idempotency_key": "wrong-target-node-restore",
                }
            ),
        )
