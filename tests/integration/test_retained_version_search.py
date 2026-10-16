"""Retained evidence uses an accepted historical projection and CURRENT authority."""
# ruff: noqa: F811

import hashlib

import pytest
from test_entity_resolution import TEXT
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.materials.lifecycle import MaterialLifecycle
from domain_kg.materials.models import (
    AddMaterial,
    MaterialMetadataChanges,
    RemoveMaterial,
    ReplaceMaterial,
    UpdateMaterialMetadata,
    UploadCommand,
)
from domain_kg.retrieval.models import SearchFilters, SearchRequest
from domain_kg.retrieval.service import RetrievalService

pytestmark = pytest.mark.storage


async def test_retained_version_is_searchable_in_all_branches_under_current_metadata(publishing):
    materials, editor, queue, coordinator, _, _ = publishing
    old, job, checkpoint = await prepare(
        publishing,
        command=AddMaterial(
            kg_id=editor.kg_ids[0],
            title="Earlier source",
            inline_text=TEXT + "\nLegacy-only phrase.",
            idempotency_key="retained-earlier",
        ),
    )
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    content = ("# Current source\n" + TEXT + "\nCurrent-only phrase.").encode()

    async def stream():
        yield content

    upload = await materials.upload(
        editor,
        UploadCommand(
            kg_id=job.kg_id,
            media_type="text/markdown",
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            target_material_id=old.material_id,
            idempotency_key="retained-new-upload",
        ),
        stream(),
    )
    current, successor, checkpoint = await prepare(
        publishing,
        command=ReplaceMaterial(
            kg_id=job.kg_id,
            material_id=old.material_id,
            expected_active_version=old.version_id,
            title="Current source",
            upload_id=upload.upload_id,
            idempotency_key="retained-new",
        ),
    )
    await coordinator.publish(editor, await ready(publishing, successor, checkpoint))
    await MaterialLifecycle(materials, queue).update_metadata(
        editor,
        UpdateMaterialMetadata(
            kg_id=job.kg_id,
            material_id=old.material_id,
            expected_revision=2,
            idempotency_key="retained-live-tags",
            changes=MaterialMetadataChanges(tags=("live",)),
        ),
    )
    service = RetrievalService(queue, coordinator.providers)
    request = SearchRequest(
        kg_ids=editor.kg_ids,
        query="Alpha",
        mode="lexical",
        filters=SearchFilters(
            material_ids=(old.material_id,),
            version_id=old.version_id,
            tags_all=("live",),
            media_types=("text/plain",),
        ),
    )
    for mode in ("lexical", "vector", "graph", "fast", "deep"):
        result = await service.search(editor, request.model_copy(update={"mode": mode}))
        assert result.results and {hit.version_id for hit in result.results} == {old.version_id}
        assert all(
            "Legacy-only" in hit.text and "Current-only" not in hit.text for hit in result.results
        )
        assert result.kg_revisions[job.kg_id] == 2
    active = await service.search(
        editor,
        request.model_copy(update={"filters": SearchFilters(material_ids=(old.material_id,))}),
    )
    assert {hit.version_id for hit in active.results} == {current.version_id}


async def test_retained_reads_recheck_current_publication_and_removal_fences(publishing):
    materials, editor, queue, coordinator, _, _ = publishing
    old, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    current, successor, checkpoint = await prepare(
        publishing,
        command=ReplaceMaterial(
            kg_id=job.kg_id,
            material_id=old.material_id,
            expected_active_version=old.version_id,
            title="Current",
            inline_text=TEXT + "\nUpdated.",
            idempotency_key="retained-current",
        ),
    )
    await coordinator.publish(editor, await ready(publishing, successor, checkpoint))
    service = RetrievalService(queue, coordinator.providers)
    request = SearchRequest(
        kg_ids=editor.kg_ids,
        query="Alpha",
        mode="lexical",
        filters=SearchFilters(material_ids=(old.material_id,), version_id=old.version_id),
    )
    scope = await service.resolver.resolve(editor, request, job.kg_id)
    assert {source.version_id for source in scope.visible_sources} == {old.version_id}
    unrelated, third, checkpoint = await prepare(publishing, key="retained-other-material")
    await coordinator.publish(editor, await ready(publishing, third, checkpoint))
    with pytest.raises(DomainError) as error:
        await service.resolver.revalidate(scope)
    assert error.value.code == ErrorCode.POLICY_CHANGED
    staged = await materials.replace(
        editor,
        ReplaceMaterial(
            kg_id=job.kg_id,
            material_id=old.material_id,
            expected_active_version=current.version_id,
            title="Unpublished",
            inline_text=TEXT,
            idempotency_key="retained-staged",
        ),
    )
    for version, code in (
        (staged.version_id, ErrorCode.REVISION_NOT_READY),
        (unrelated.version_id, ErrorCode.RESOURCE_NOT_ACCESSIBLE),
    ):
        with pytest.raises(DomainError) as error:
            await service.search(
                editor,
                request.model_copy(
                    update={
                        "filters": SearchFilters(
                            material_ids=(old.material_id,), version_id=version
                        )
                    }
                ),
            )
        assert error.value.code == code
    await MaterialLifecycle(materials, queue).remove(
        editor,
        RemoveMaterial(
            kg_id=job.kg_id,
            material_id=old.material_id,
            action="delete",
            expected_revision=2,
            idempotency_key="retained-remove",
        ),
    )
    with pytest.raises(DomainError):
        await service.resolver.revalidate(scope)
    assert (await service.search(editor, request)).results == ()
    with pytest.raises(DomainError):
        _ = [
            part
            async for part in materials.original(editor, job.kg_id, old.material_id, old.version_id)
        ]


async def test_retained_version_does_not_restore_prior_material_grants(publishing, team):
    from domain_kg.identity.models import Action

    materials, editor, queue, coordinator, _, _ = publishing
    old, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    _, successor, checkpoint = await prepare(
        publishing,
        command=ReplaceMaterial(
            kg_id=job.kg_id,
            material_id=old.material_id,
            expected_active_version=old.version_id,
            title="Successor",
            inline_text=TEXT + "\nChanged.",
            idempotency_key="grants-successor",
        ),
    )
    await coordinator.publish(editor, await ready(publishing, successor, checkpoint))
    service = RetrievalService(queue, coordinator.providers)
    request = SearchRequest(
        kg_ids=editor.kg_ids,
        query="Alpha",
        mode="lexical",
        filters=SearchFilters(material_ids=(old.material_id,), version_id=old.version_id),
    )
    reader = await queue.policy.resolve_access(team[2]["reader"], [job.kg_id], Action.SEARCH)
    scope = await service.resolver.resolve(reader, request, job.kg_id)
    assert scope.visible_sources
    owner = await queue.policy.resolve_access(team[2]["owner"], [job.kg_id], Action.MANAGE_GRANTS)
    await queue.policy.set_material_acl(
        owner,
        job.kg_id,
        old.material_id,
        "restricted",
        member_ids=(team[3]["editor"].id,),
        group_ids=(),
        idempotency_key="retained-grant-change",
    )
    with pytest.raises(DomainError):
        await service.lexical.retrieve(scope, request)
    reader = await queue.policy.resolve_access(team[2]["reader"], [job.kg_id], Action.SEARCH)
    assert (await service.search(reader, request)).results == ()


async def test_retained_source_keeps_current_source_scoped_human_corrections(publishing):
    from test_curation import alpha_id, publish_curation

    from domain_kg.graph.service import GraphService
    from domain_kg.revisions.models import NodeChanges, NodeUpdate
    from domain_kg.revisions.service import RevisionService

    _, editor, queue, coordinator, _, _ = publishing
    old, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    alpha = await alpha_id(queue, editor, job.kg_id)
    correction = await RevisionService(queue, coordinator.providers).update_node(
        editor,
        NodeUpdate(
            kg_id=job.kg_id,
            entity_id=alpha,
            expected_revision=0,
            source_ids=(old.material_id,),
            changes=NodeChanges(name="Reviewed Alpha"),
            reason="Persistent human terminology",
            idempotency_key="retained-current-correction",
        ),
    )
    await publish_curation(queue, coordinator, editor, correction)
    current, successor, checkpoint = await prepare(
        publishing,
        command=ReplaceMaterial(
            kg_id=job.kg_id,
            material_id=old.material_id,
            expected_active_version=old.version_id,
            title="Successor",
            inline_text=TEXT,
            idempotency_key="retained-after-correction",
        ),
    )
    await coordinator.publish(editor, await ready(publishing, successor, checkpoint))
    filters = SearchFilters(material_ids=(old.material_id,), version_id=old.version_id)
    node = await GraphService(queue).get_node(editor, job.kg_id, alpha, filters)
    assert node.name == "Reviewed Alpha"
    assert {item.version_id for item in node.observations} == {old.version_id}
    assert current.version_id != old.version_id
    response = await RetrievalService(queue, coordinator.providers).search(
        editor,
        SearchRequest(
            kg_ids=editor.kg_ids, query="Reviewed Alpha", mode="lexical", filters=filters
        ),
    )
    assert any(item.curation_revision_id == correction.revision_id for item in response.results)
    assert response.kg_revisions[job.kg_id] == 3
