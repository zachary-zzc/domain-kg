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


async def test_replacement_reuses_unchanged_windows_with_new_source_ownership(
    material_service, team, monkeypatch
):
    from test_entity_resolution import batch_for
    from test_publication import make_publishing

    from domain_kg.ingestion.models import ChunkConfig
    from domain_kg.ingestion.pipeline import extraction_steps, preparation_steps
    from domain_kg.materials.models import AddMaterial
    from domain_kg.revisions.journal import PublicationJournal
    from domain_kg.revisions.publication import accepted_document

    def extraction(chunk, profile):
        batch = batch_for(chunk, profile)
        if "Alpha" not in chunk.text:
            return batch.model_copy(update={"entities": (), "assertions": ()})
        return batch.model_copy(
            update={
                "entities": tuple(
                    entity.model_copy(update={"identifier": None}) for entity in batch.entities
                )
            }
        )

    fixture = await make_publishing(material_service, team, extraction_factory=extraction)
    materials, editor, queue, coordinator, _, faults = fixture
    preparation = preparation_steps(
        queue,
        providers=coordinator.providers,
        chunk_config=ChunkConfig(target_tokens=60, overlap_tokens=0),
    )
    steps = preparation + extraction_steps(queue, coordinator.providers, preparation[-1])
    fixture = (*fixture[:4], steps, faults)
    calls = []
    extract = coordinator.providers.extract

    async def observed(access, chunk, schema):
        calls.append(chunk.text)
        return await extract(access, chunk, schema)

    monkeypatch.setattr(coordinator.providers, "extract", observed)
    first, job, checkpoint = await prepare(
        fixture,
        command=AddMaterial(
            kg_id=editor.kg_ids[0],
            title="Delta fixture",
            inline_text=TEXT + "\n" + "Appendix " * 80,
            idempotency_key="delta-original",
        ),
    )
    await coordinator.publish(editor, await ready(fixture, job, checkpoint))
    journal = PublicationJournal(queue)
    old_binding = (await journal.load_metadata(editor, job.kg_id)).projections[0]
    old, _, _ = await accepted_document(queue, editor, old_binding)
    old_entities = {row.observation.name: row.observation for row in old.rows if row.observation}
    old_rows = tuple(old.rows)
    calls.clear()
    faults["embedding_inputs"].clear()
    second, successor, checkpoint = await prepare(
        fixture,
        command=ReplaceMaterial(
            kg_id=job.kg_id,
            material_id=first.material_id,
            expected_active_version=first.version_id,
            title="Delta successor",
            inline_text=TEXT + "\n" + "Endnote " * 80,
            idempotency_key="delta-replacement",
        ),
    )
    assert calls and all("Alpha" not in text for text in calls)
    await coordinator.publish(editor, await ready(fixture, successor, checkpoint))
    assert faults["embedding_inputs"]
    assert all("Alpha" not in text and "Beta" not in text for text in faults["embedding_inputs"])
    new_binding = (await journal.load_metadata(editor, job.kg_id)).projections[0]
    new, _, _ = await accepted_document(queue, editor, new_binding)
    assert new.version_id == second.version_id != old.version_id
    for row in new.rows:
        assert row.source.version_id == second.version_id
        if row.observation:
            previous = old_entities[row.observation.name]
            assert row.observation.entity == previous.entity
            assert row.observation.id != previous.id
            assert row.observation.spans[0].location.block_id != previous.spans[0].location.block_id
        if row.assertion:
            assert row.assertion.support.version.id == second.version_id
            assert row.assertion.support.id not in {
                item.assertion.support.id for item in old.rows if item.assertion
            }
    assert (await accepted_document(queue, editor, old_binding))[0].rows == old_rows
    # A distinct material with the same text cannot borrow this private source cache.
    calls.clear()
    faults["embedding_inputs"].clear()
    _, other, checkpoint = await prepare(
        fixture,
        command=AddMaterial(
            kg_id=job.kg_id,
            title="Independent ownership",
            inline_text=TEXT,
            idempotency_key="delta-other-material",
        ),
    )
    await ready(fixture, other, checkpoint)
    assert any("Alpha" in text for text in calls)
    assert any("Alpha" in text for text in faults["embedding_inputs"])


async def test_identical_replacement_uses_no_models_and_still_enforces_clearance(
    publishing, team, monkeypatch
):

    materials, editor, queue, coordinator, steps, _ = publishing
    first, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))

    async def forbidden(*args, **kwargs):
        pytest.fail("unchanged accepted extraction and vectors must be reused")

    monkeypatch.setattr(coordinator.providers, "extract", forbidden)
    monkeypatch.setattr(coordinator.providers, "embeddings", forbidden)
    second, successor, checkpoint = await prepare(
        publishing,
        command=ReplaceMaterial(
            kg_id=job.kg_id,
            material_id=first.material_id,
            expected_active_version=first.version_id,
            title="Same content",
            inline_text=TEXT,
            idempotency_key="identical-successor",
        ),
    )
    await coordinator.publish(editor, await ready(publishing, successor, checkpoint))
    # An editor retains source access, but this profile has no restricted-data
    # clearance. Even a completely cached replacement must fail before reuse.
    await queue.database.material_access.update_one(
        {"org_id": str(job.org_id), "kg_id": str(job.kg_id), "id": str(first.material_id)},
        {"$set": {"acl_mode": "restricted", "member_ids": [str(team[3]["editor"].id)]}},
    )
    pending = await materials.replace(
        editor,
        ReplaceMaterial(
            kg_id=job.kg_id,
            material_id=first.material_id,
            expected_active_version=second.version_id,
            title="Restricted content",
            inline_text=TEXT,
            idempotency_key="restricted-cache",
        ),
    )
    leased = await queue.claim("restricted-cache-check")
    assert leased.id == pending.job_id
    for step in steps[:3]:
        checkpoint = await step.execute(leased)
        await queue.checkpoint(leased, checkpoint)
    with pytest.raises(DomainError) as error:
        await steps[3].execute(leased)
    assert error.value.code == ErrorCode.RESOURCE_NOT_ACCESSIBLE
    assert (await queue.database.material_access.find_one({"id": str(first.material_id)}))[
        "active_version_id"
    ] == str(second.version_id)


@pytest.mark.parametrize("change", ["endpoint", "extraction_weights"])
async def test_changed_extraction_origin_does_not_reuse_model_output(
    publishing, monkeypatch, change
):
    materials, editor, queue, coordinator, _, faults = publishing
    first, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    registry = coordinator.providers.registry
    profile_id, profile = next(iter(registry.profiles.items()))
    if change == "endpoint":
        connection = registry.connections[profile.connection_ref]
        registry.connections[profile.connection_ref] = connection.model_copy(
            update={"endpoint": "https://provider-v2.example/v1"}
        )
        # Keep embedding space unchanged by assigning its original connection.
        registry.connections["original-embedding"] = connection
        profile = profile.model_copy(update={"embedding_connection_ref": "original-embedding"})
    else:
        profile = profile.model_copy(update={"extraction_revision": "weights-v2"})
    registry.profiles[profile_id] = profile
    calls = []
    extract = coordinator.providers.extract

    async def observed(access, chunk, schema):
        calls.append(chunk.text)
        return await extract(access, chunk, schema)

    monkeypatch.setattr(coordinator.providers, "extract", observed)
    _, successor, checkpoint = await prepare(
        publishing,
        command=ReplaceMaterial(
            kg_id=job.kg_id,
            material_id=first.material_id,
            expected_active_version=first.version_id,
            title="New extraction configuration",
            inline_text=TEXT,
            idempotency_key=f"changed-{change}",
        ),
    )
    assert calls == [TEXT]
    faults["embedding_inputs"].clear()
    await ready(publishing, successor, checkpoint)
    assert faults["embedding_inputs"] == []  # Exact text and embedding space are unchanged.
