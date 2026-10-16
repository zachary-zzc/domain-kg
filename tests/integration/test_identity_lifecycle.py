"""Stored identity members cannot reappear after compensation or source replacement."""
# ruff: noqa: F811

import pytest
from test_entity_resolution import TEXT
from test_identity_boundaries import pending_identity
from test_identity_curation import finish_identity
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.materials.models import ReplaceMaterial
from domain_kg.revisions.models import CurationCommand, RollbackRevision

pytestmark = pytest.mark.storage


async def test_group_suppression_survives_identity_compensation_and_reingestion(publishing, team):
    _, editor, queue, coordinator, _, _ = publishing
    source, alpha, beta, service, command = await pending_identity(publishing)
    merged = await service.curate_node(editor, command)
    await finish_identity(queue, coordinator, editor, merged)
    fence = CurationCommand(
        kg_id=command.kg_id,
        entity_id=alpha,
        action="suppress",
        expected_revision=1,
        source_ids=(source.material_id,),
        reason="Withdraw the reviewed identity group",
        idempotency_key="group-suppression",
    )
    await service.curate_node(editor, fence)
    owner = await queue.policy.resolve_access(team[2]["owner"], [command.kg_id], Action.CURATE)
    compensation = await service.rollback(
        owner,
        RollbackRevision(
            kg_id=command.kg_id,
            revision_id=merged.revision_id,
            expected_current_revision=2,
            reason="Withdraw grouping while keeping suppression",
            idempotency_key="hidden-undo-merge",
        ),
    )
    await finish_identity(queue, coordinator, owner, compensation)
    graph = GraphService(queue)
    with pytest.raises(DomainError):
        await graph.get_node(editor, command.kg_id, beta)
    _, replacement, checkpoint = await prepare(
        publishing,
        command=ReplaceMaterial(
            kg_id=command.kg_id,
            material_id=source.material_id,
            expected_active_version=source.version_id,
            title="Updated original",
            inline_text=TEXT + "\nA harmless source update.",
            idempotency_key="hidden-group-replace",
        ),
    )
    await coordinator.publish(editor, await ready(publishing, replacement, checkpoint))
    with pytest.raises(DomainError):
        await graph.get_node(editor, command.kg_id, beta)
    await service.curate_node(
        editor,
        fence.model_copy(
            update={
                "action": "restore",
                "expected_revision": 3,
                "idempotency_key": "restore-split-group",
            }
        ),
    )
    node = await graph.get_node(editor, command.kg_id, beta)
    assert node.id == beta and node.name == "Beta"
