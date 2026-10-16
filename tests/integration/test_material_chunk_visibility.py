"""Retained source inspection honors current suppression without recovering old text."""
# ruff: noqa: F811

import pytest
from test_curation import alpha_id
from test_entity_resolution import TEXT
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.identity.models import Action
from domain_kg.materials.inspection import MaterialInspection
from domain_kg.materials.models import ReplaceMaterial
from domain_kg.revisions.models import CurationCommand
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


async def test_current_and_retained_material_chunks_share_current_node_fences(publishing, team):
    _, editor, queue, coordinator, _, _ = publishing
    first, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    second, replacement, checkpoint = await prepare(
        publishing,
        command=ReplaceMaterial(
            kg_id=job.kg_id,
            material_id=first.material_id,
            title="Replacement inspection fixture",
            expected_active_version=first.version_id,
            inline_text=TEXT,
            idempotency_key="inspect-replacement",
        ),
    )
    await coordinator.publish(editor, await ready(publishing, replacement, checkpoint))
    reader = await queue.policy.resolve_access(team[2]["reader"], [job.kg_id], Action.READ)
    service = MaterialInspection(queue)
    current = await service.get(reader, job.kg_id, first.material_id)
    retained = await service.get(reader, job.kg_id, first.material_id, first.version_id)
    assert {version.id for version in retained.versions} == {first.version_id, second.version_id}
    assert current.chunks and retained.chunks
    assert {chunk.version_id for chunk in current.chunks} == {second.version_id}
    assert {chunk.version_id for chunk in retained.chunks} == {first.version_id}
    assert (
        current.material.active_version_id
        == retained.material.active_version_id
        == second.version_id
    )
    alpha = await alpha_id(queue, editor, job.kg_id)
    await RevisionService(queue, coordinator.providers).curate_node(
        editor,
        CurationCommand(
            kg_id=job.kg_id,
            entity_id=alpha,
            action="suppress",
            expected_revision=0,
            curation_scope="source",
            source_ids=(first.material_id,),
            reason="Suppressed source fields must not appear in material inspection",
            idempotency_key="inspection-suppression",
        ),
    )
    for version in (None, first.version_id):
        detail = await service.get(reader, job.kg_id, first.material_id, version)
        assert not detail.chunks and not detail.chunks_truncated
        assert TEXT not in detail.model_dump_json()
