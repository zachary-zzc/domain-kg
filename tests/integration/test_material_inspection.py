"""Material management reads enforce current ACLs before paging or returning fields."""

import pytest
from test_entity_resolution import TEXT
from test_publication import make_publishing, prepare, ready

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.materials.inspection import MaterialInspection
from domain_kg.materials.lifecycle import MaterialLifecycle
from domain_kg.materials.models import AddMaterial, RemoveMaterial
from domain_kg.models import PageRequest
from domain_kg.retrieval.models import SearchFilters

pytestmark = pytest.mark.storage


async def test_material_pages_and_inspection_never_include_hidden_source_fields(
    material_service, team
):
    fixture = await make_publishing(material_service, team, restricted=True)
    materials, editor, queue, coordinator, _, _ = fixture
    sources = []
    for key, acl in (("private-material", "restricted"), ("team-one", "kg"), ("team-two", "kg")):
        source, job, checkpoint = await prepare(
            fixture,
            command=AddMaterial(
                kg_id=editor.kg_ids[0],
                title=key,
                inline_text=TEXT,
                acl_mode=acl,
                idempotency_key=key,
            ),
        )
        await coordinator.publish(editor, await ready(fixture, job, checkpoint))
        sources.append(source)
    kg = job.kg_id
    inspection = MaterialInspection(queue)
    reader = await queue.policy.resolve_access(team[2]["reader"], [kg], Action.READ)
    page = await inspection.list(reader, kg, PageRequest(limit=1), SearchFilters())
    assert len(page.items) == 1 and page.next_cursor
    assert page.items[0].id in {source.material_id for source in sources[1:]}
    assert "private-material" not in page.model_dump_json()
    second = await inspection.list(
        reader, kg, PageRequest(limit=1, cursor=page.next_cursor), SearchFilters()
    )
    assert len(second.items) == 1 and second.items[0].id != page.items[0].id
    with pytest.raises(DomainError):
        await inspection.get(reader, kg, sources[0].material_id)
    with pytest.raises(DomainError) as wrong_page:
        await inspection.list(
            reader, kg, PageRequest(limit=2, cursor=page.next_cursor), SearchFilters()
        )
    assert wrong_page.value.code == ErrorCode.CURSOR_EXPIRED
    detail = await inspection.get(reader, kg, sources[1].material_id)
    assert detail.material.title == "team-one"
    assert detail.versions and detail.versions[0].id == sources[1].version_id
    assert detail.chunks and detail.chunks[0].text == TEXT
    assert detail.chunks[0].version_id == sources[1].version_id
    assert detail.chunks[0].spans and not detail.chunks_truncated
    assert "blob" not in detail.model_dump_json() and "object_id" not in detail.model_dump_json()
    await MaterialLifecycle(materials, queue).remove(
        editor,
        RemoveMaterial(
            kg_id=kg,
            material_id=sources[1].material_id,
            action="delete",
            expected_revision=1,
            idempotency_key="inspection-remove",
        ),
    )
    with pytest.raises(DomainError) as expired:
        await inspection.list(
            reader, kg, PageRequest(limit=1, cursor=page.next_cursor), SearchFilters()
        )
    assert expired.value.code == ErrorCode.CURSOR_EXPIRED
    with pytest.raises(DomainError):
        await inspection.get(reader, kg, sources[1].material_id, sources[1].version_id)
