"""Metadata-only revisions retain vectors and fence stale content publication."""
# ruff: noqa: F811

import pytest
from test_entity_resolution import TEXT
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.materials.lifecycle import MaterialLifecycle
from domain_kg.materials.models import (
    MaterialMetadataChanges,
    ReplaceMaterial,
    UpdateMaterialMetadata,
)
from domain_kg.models import ScopedRef
from domain_kg.retrieval.lexical import LexicalRetrievalBranch
from domain_kg.retrieval.models import SearchFilters, SearchRequest

pytestmark = pytest.mark.storage


async def test_metadata_changes_do_not_call_models_and_apply_to_live_filters(
    publishing, monkeypatch
):
    materials, editor, queue, coordinator, _, _ = publishing
    receipt, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    branch = LexicalRetrievalBranch(queue)
    request = SearchRequest(kg_ids=editor.kg_ids, query="Alpha", mode="lexical")
    old_scope = await branch.resolver.resolve(editor, request, job.kg_id)
    original_stores = await queue.router.for_sources(editor, job.kg_id)

    async def forbidden(*args, **kwargs):
        pytest.fail("metadata-only updates must not call models or derived backends")

    command = UpdateMaterialMetadata(
        kg_id=job.kg_id,
        material_id=receipt.material_id,
        expected_revision=1,
        idempotency_key="metadata",
        changes=MaterialMetadataChanges(
            title="Revised source", tags=("updated",), author=None, language="zh"
        ),
    )
    lifecycle = MaterialLifecycle(materials, queue)
    with monkeypatch.context() as patch:
        patch.setattr(coordinator.providers, "extract", forbidden)
        patch.setattr(coordinator.providers, "embeddings", forbidden)
        patch.setattr(queue.router, "for_kg", forbidden)
        updated = await lifecycle.update_metadata(editor, command)
        assert await lifecycle.update_metadata(editor, command) == updated
    assert updated.revision == 2 and updated.version_id == receipt.version_id
    with pytest.raises(DomainError) as error:
        await branch.resolver.revalidate(old_scope)
    assert error.value.code == ErrorCode.POLICY_CHANGED
    filtered = request.model_copy(
        update={"filters": SearchFilters(tags_all=("updated",), languages=("zh",))}
    )
    scope = await branch.resolver.resolve(editor, filtered, job.kg_id)
    assert [item.material_id for item in await branch.retrieve(scope, filtered)] == [
        receipt.material_id
    ]
    record = await original_stores.authority._require(
        "materials", ScopedRef(org_id=job.org_id, kg_id=job.kg_id, id=receipt.material_id)
    )
    assert record["title"] == "Revised source"
    assert (
        await queue.database.jobs.count_documents(
            {"org_id": str(job.org_id), "kg_id": str(job.kg_id)}
        )
        == 1
    )


async def test_metadata_revision_racing_prepared_replacement_cannot_be_overwritten(publishing):
    materials, editor, queue, coordinator, _, _ = publishing
    first, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    _, replacement, checkpoint = await prepare(
        publishing,
        command=ReplaceMaterial(
            kg_id=job.kg_id,
            material_id=first.material_id,
            expected_active_version=first.version_id,
            title="replacement",
            inline_text=TEXT + "\nA new paragraph.",
            tags=("old",),
            idempotency_key="queued-before-metadata",
        ),
    )
    publication = await ready(publishing, replacement, checkpoint)
    lifecycle = MaterialLifecycle(materials, queue)
    await lifecycle.update_metadata(
        editor,
        UpdateMaterialMetadata(
            kg_id=job.kg_id,
            material_id=first.material_id,
            expected_revision=1,
            idempotency_key="later-metadata",
            changes=MaterialMetadataChanges(tags=("newer",)),
        ),
    )
    with pytest.raises(DomainError) as error:
        await coordinator.publish(editor, publication)
    assert error.value.code == ErrorCode.VERSION_CONFLICT
    row = await queue.database.material_access.find_one(
        {"org_id": str(job.org_id), "kg_id": str(job.kg_id), "id": str(first.material_id)}
    )
    assert row["metadata"]["tags"] == ["newer"] and row["active_version_id"] == str(
        first.version_id
    )


async def test_metadata_updates_reject_stale_revisions_and_readers(publishing, team):
    materials, editor, queue, coordinator, _, _ = publishing
    receipt, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    lifecycle = MaterialLifecycle(materials, queue)
    command = UpdateMaterialMetadata(
        kg_id=job.kg_id,
        material_id=receipt.material_id,
        expected_revision=0,
        idempotency_key="stale-metadata",
        changes=MaterialMetadataChanges(tags=()),
    )
    with pytest.raises(DomainError) as error:
        await lifecycle.update_metadata(editor, command)
    assert error.value.code == ErrorCode.VERSION_CONFLICT
    reader = await queue.policy.resolve_access(team[2]["reader"], [job.kg_id], Action.READ)
    with pytest.raises(DomainError):
        await lifecycle.update_metadata(reader, command.model_copy(update={"expected_revision": 1}))
