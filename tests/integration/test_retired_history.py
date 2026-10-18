"""Purged source history cannot block independent human knowledge or resurrect bytes."""
# ruff: noqa: F811

from datetime import UTC, datetime, timedelta

import pytest
from test_curation import alpha_id, publish_curation
from test_publication import prepare, publishing, ready  # noqa: F401
from test_restore_preparation import setup_restore

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.materials.lifecycle import MaterialLifecycle
from domain_kg.materials.models import RemoveMaterial
from domain_kg.materials.purge_models import PurgeMaterial
from domain_kg.operations.backup import BackupService
from domain_kg.operations.generation import GenerationService
from domain_kg.operations.generation_capture import GenerationCapture
from domain_kg.operations.generation_preparation import GenerationPreparation
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import BackupCommand, GenerationCommand
from domain_kg.operations.restore import RestoreService
from domain_kg.retrieval.models import SearchRequest
from domain_kg.retrieval.service import RetrievalService
from domain_kg.revisions.journal import PublicationJournal
from domain_kg.revisions.models import NodeChanges, NodeUpdate, RelationChanges, RelationUpdate
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


async def retire_with_human_history(publishing, team, source):
    materials, editor, queue, coordinator, _, _ = publishing
    alpha = await alpha_id(queue, editor, source.kg_id)
    revisions = RevisionService(queue, coordinator.providers)
    renamed = await revisions.update_node(
        editor,
        NodeUpdate(
            kg_id=source.kg_id,
            entity_id=alpha,
            expected_revision=0,
            source_ids=(source.material_id,),
            changes=NodeChanges(name="Private retired name"),
            reason="Preserve scoped human history after retirement",
            idempotency_key="retired-scoped-history",
        ),
    )
    await publish_curation(queue, coordinator, editor, renamed)
    graph = GraphService(queue)
    scope = await graph.resolver.resolve(
        editor, SearchRequest(kg_ids=(source.kg_id,), query="Alpha"), source.kg_id
    )
    fact = (await graph.repository.open(scope)).supports[0].claim.assertion
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.CURATE)
    public = await revisions.update_relation(
        owner,
        RelationUpdate(
            kg_id=source.kg_id,
            assertion_id=fact.id,
            expected_revision=0,
            curation_scope="kg_public",
            changes=RelationChanges(
                subject_id=fact.subject.id,
                target_id=fact.target.id,
                predicate="related_to",
                description="Independent human knowledge",
            ),
            reason="Independent manual claim survives original erasure",
            idempotency_key="retired-independent-history",
        ),
    )
    await publish_curation(queue, coordinator, owner, public)
    lifecycle = MaterialLifecycle(materials, queue)
    removed = await lifecycle.remove(
        editor,
        RemoveMaterial(
            kg_id=source.kg_id,
            material_id=source.material_id,
            action="delete",
            expected_revision=1,
            idempotency_key="retired-remove",
        ),
    )
    ns = {"org_id": str(source.org_id), "kg_id": str(source.kg_id)}
    await queue.database.material_access.update_one(
        ns | {"id": str(source.material_id)},
        {"$set": {"purge_after": datetime.now(UTC) - timedelta(seconds=1)}},
    )
    await lifecycle.purge(
        owner,
        PurgeMaterial(
            kg_id=source.kg_id,
            material_id=source.material_id,
            expected_revision=removed.revision,
            reason="Expired synthetic original",
            idempotency_key="retired-purge",
        ),
    )
    job = await queue.claim("retired-purger", kinds=("purge_material",))
    await JobRunner(queue, configured_pipeline(queue, coordinator.providers)).run_job(job)
    assert (await queue.get(owner, job.id)).stage == "ready"
    assert (await queue.database.material_access.find_one(ns | {"id": str(source.material_id)}))[
        "purged"
    ]
    return renamed.revision_id, public.revision_id


def forbid_models(monkeypatch, providers):
    async def forbidden(*args, **kwargs):
        pytest.fail("retired-history operations must not reconstruct original text with a model")

    monkeypatch.setattr(providers, "extract", forbidden)
    monkeypatch.setattr(providers, "embeddings", forbidden)


async def test_rebuild_keeps_curation_only_dependencies_after_real_source_purge(
    publishing, team, monkeypatch
):
    _, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    history = await retire_with_human_history(publishing, team, source)
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.CONFIGURE)
    before = await PublicationJournal(queue).load_metadata(owner, source.kg_id)
    assert not before.sources and len(before.curations) == 2
    forbid_models(monkeypatch, coordinator.providers)
    await OwnerOperations(queue).submit(
        owner,
        GenerationCommand(
            kg_id=source.kg_id,
            expected_published_revision=before.revision,
            idempotency_key="retired-rebuild",
        ),
    )
    rebuild = await queue.claim("retired-rebuilder", kinds=("generation",))
    capture = GenerationCapture(queue)
    inputs = await capture.start(rebuild)
    dependent = next(item for item in inputs if item.dependencies)
    pin = await queue.database.read_pins.find_one(capture.pin_key(rebuild))
    assert str(source.material_id) in pin["material_ids"]
    key = {"org_id": str(source.org_id), "kg_id": str(source.kg_id), "id": str(source.material_id)}
    await queue.database.material_access.update_one(key, {"$inc": {"revision": 1}})
    with pytest.raises(DomainError) as changed:
        await GenerationPreparation(queue).document(rebuild, dependent)
    assert changed.value.code == ErrorCode.VERSION_CONFLICT
    await queue.database.material_access.update_one(key, {"$inc": {"revision": -1}})
    await GenerationService(queue).run(rebuild)
    assert await queue.accepted(rebuild)
    after = await PublicationJournal(queue).load_metadata(owner, source.kg_id)
    assert not after.sources and {ref.id for ref in after.curations} == set(history)
    found = await RetrievalService(queue, coordinator.providers).search(
        owner,
        SearchRequest(kg_ids=(source.kg_id,), query="Independent human knowledge", mode="lexical"),
    )
    assert found.results and all(hit.curation_revision_id == history[1] for hit in found.results)
    assert all("Private retired name" not in hit.text for hit in found.results)


async def test_backup_restore_keeps_retired_history_without_original_bytes(
    publishing, team, catalog, monkeypatch
):
    _, _, queue, coordinator, _, _ = publishing
    history = []

    async def before_backup(source):
        history.extend(await retire_with_human_history(publishing, team, source))
        forbid_models(monkeypatch, coordinator.providers)

    source, _, job, target, _ = await setup_restore(
        publishing, team, catalog, before_backup=before_backup
    )
    await RestoreService(queue, coordinator.providers).run(job)
    owner = await queue.policy.resolve_access(team[2]["owner"], [target.target.kg_id], Action.READ)
    ns = target.target.model_dump(mode="json")
    material = await queue.database.material_access.find_one(ns | {"id": str(source.material_id)})
    assert material["purged"] and material["active_version_id"] is None
    assert material["tombstoned"] and "metadata" not in material
    assert await queue.database.curation_revisions.count_documents(ns | {"state": "accepted"}) == 2
    view = await PublicationJournal(queue).load_metadata(owner, target.target.kg_id)
    assert not view.sources and {ref.id for ref in view.curations} == set(history)
    found = await RetrievalService(queue, coordinator.providers).search(
        owner,
        SearchRequest(
            kg_ids=(target.target.kg_id,), query="Independent human knowledge", mode="lexical"
        ),
    )
    assert found.results and all(hit.curation_revision_id == history[1] for hit in found.results)
    assert all("Private retired name" not in hit.text for hit in found.results)
    await OwnerOperations(queue).submit(
        owner,
        BackupCommand(
            kg_id=target.target.kg_id,
            expected_published_revision=view.revision,
            idempotency_key="backup-retired-restored",
        ),
    )
    backup = await queue.claim("retired-restored-capture", kinds=("backup",))
    service = BackupService(queue)
    await service.run(backup)
    manifest = await service.verify(owner, target.target.kg_id, backup.target_id)
    assert manifest.source_count == 0 and manifest.curation_count == 2
    for index in range(len(manifest.pages)):
        page = await service.parts(owner, target.target.kg_id, backup.target_id, index)
        assert not any(part.kind in ("original", "source_artifact") for part in page.parts)
