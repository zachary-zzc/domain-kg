"""Compensation restores manual field state under current authority, never old grants."""
# ruff: noqa: F811

import pytest
from test_curation import alpha_id, publish_curation
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.materials.lifecycle import MaterialLifecycle
from domain_kg.materials.models import RemoveMaterial
from domain_kg.revisions.models import NodeChanges, NodeUpdate, RollbackRevision
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


async def test_rollback_compensates_only_selected_fields_without_reembedding(
    publishing, team, monkeypatch
):
    materials, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    alpha = await alpha_id(queue, editor, job.kg_id)
    service = RevisionService(queue, coordinator.providers)
    command = NodeUpdate(
        kg_id=job.kg_id,
        entity_id=alpha,
        expected_revision=0,
        source_ids=(source.material_id,),
        changes=NodeChanges(name="Human label"),
        reason="Initial review",
        idempotency_key="rollback-name",
    )
    original = await service.update_node(editor, command)
    await publish_curation(queue, coordinator, editor, original)
    metadata = await service.update_node(
        editor,
        command.model_copy(
            update={
                "expected_revision": 1,
                "changes": NodeChanges(tags=("keep-this",)),
                "idempotency_key": "rollback-later-tags",
            }
        ),
    )
    await publish_curation(queue, coordinator, editor, metadata)
    original_record = await queue.database.curation_revisions.find_one(
        {"id": str(original.revision_id)}
    )
    rollback = RollbackRevision(
        kg_id=job.kg_id,
        revision_id=original.revision_id,
        expected_current_revision=2,
        reason="Withdraw this name correction, preserve later tags.",
        idempotency_key="rollback-compensation",
    )
    with pytest.raises(DomainError):
        await service.rollback(editor, rollback)
    owner = await queue.policy.resolve_access(team[2]["owner"], [job.kg_id], Action.CURATE)

    async def forbidden(*args, **kwargs):
        pytest.fail("clearing the only embedded override requires no new embedding")

    with monkeypatch.context() as patch:
        patch.setattr(coordinator.providers, "embeddings", forbidden)
        compensation = await service.rollback(owner, rollback)
        assert await service.rollback(owner, rollback) == compensation
        await publish_curation(queue, coordinator, owner, compensation)
    node = await GraphService(queue).get_node(owner, job.kg_id, alpha)
    assert node.name == "Alpha" and node.tags == ("keep-this",)
    assert (
        await queue.database.curation_revisions.find_one({"id": str(original.revision_id)})
        == original_record
    )
    saved = await queue.database.curation_revisions.find_one({"id": str(compensation.revision_id)})
    assert saved["rollback_of"] == str(original.revision_id)
    assert saved["after"] == {"tags": ["keep-this"]}
    await MaterialLifecycle(materials, queue).remove(
        editor,
        RemoveMaterial(
            kg_id=job.kg_id,
            material_id=source.material_id,
            action="delete",
            expected_revision=1,
            idempotency_key="rollback-source-withdrawn",
        ),
    )
    with pytest.raises(DomainError):
        await service.rollback(
            owner,
            rollback.model_copy(
                update={"expected_current_revision": 3, "idempotency_key": "rollback-after-delete"}
            ),
        )


async def test_rollback_withdraws_the_last_public_field_without_exposing_private_defaults(
    material_service, team, monkeypatch
):
    from test_entity_resolution import TEXT
    from test_publication import make_publishing

    from domain_kg.materials.models import AddMaterial
    from domain_kg.retrieval.models import SearchRequest
    from domain_kg.retrieval.service import RetrievalService

    fixture = await make_publishing(material_service, team, restricted=True)
    _, editor, queue, coordinator, _, _ = fixture
    source, job, checkpoint = await prepare(
        fixture,
        command=AddMaterial(
            kg_id=editor.kg_ids[0],
            title="Private fallback must stay private",
            inline_text=TEXT,
            acl_mode="restricted",
            idempotency_key="rollback-private-base",
        ),
    )
    await coordinator.publish(editor, await ready(fixture, job, checkpoint))
    alpha = await alpha_id(queue, editor, job.kg_id)
    owner = await queue.policy.resolve_access(team[2]["owner"], [job.kg_id], Action.CURATE)
    reader = await queue.policy.resolve_access(team[2]["reader"], [job.kg_id], Action.SEARCH)
    service = RevisionService(queue, coordinator.providers)
    original = await service.update_node(
        owner,
        NodeUpdate(
            kg_id=job.kg_id,
            entity_id=alpha,
            expected_revision=0,
            curation_scope="kg_public",
            changes=NodeChanges(name="Public label"),
            reason="Publish only this label",
            idempotency_key="rollback-public-label",
        ),
    )
    await publish_curation(queue, coordinator, owner, original)
    assert (await GraphService(queue).get_node(reader, job.kg_id, alpha)).name == "Public label"

    async def forbidden(*args, **kwargs):
        pytest.fail("withdrawing public fields requires no model")

    with monkeypatch.context() as patch:
        patch.setattr(coordinator.providers, "embeddings", forbidden)
        compensation = await service.rollback(
            owner,
            RollbackRevision(
                kg_id=job.kg_id,
                revision_id=original.revision_id,
                expected_current_revision=1,
                reason="Withdraw the public label",
                idempotency_key="rollback-withdraw-public",
            ),
        )
        await publish_curation(queue, coordinator, owner, compensation)
    with pytest.raises(DomainError):
        await GraphService(queue).get_node(reader, job.kg_id, alpha)
    for mode in ("lexical", "vector", "graph", "deep"):
        result = await RetrievalService(queue, coordinator.providers).search(
            reader, SearchRequest(kg_ids=(job.kg_id,), query="Public label", mode=mode)
        )
        assert result.results == ()
