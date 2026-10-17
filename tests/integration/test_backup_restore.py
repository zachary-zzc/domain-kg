"""Publication-bound backup packages verify originals and authority before acceptance."""
# ruff: noqa: F811

from uuid import uuid4

import pytest
from test_entity_resolution import TEXT
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import BackupCommand
from domain_kg.operations.storage import PackageStore

pytestmark = pytest.mark.storage


async def test_backup_worker_seals_verified_originals_authority_and_projection_artifacts(
    publishing, team, monkeypatch
):
    from domain_kg.operations.backup import BackupService

    _, editor, queue, coordinator, _, _ = publishing
    source, source_job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, source_job, checkpoint))
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.BACKUP)

    async def forbidden(*args, **kwargs):
        pytest.fail("backup capture must not extract or embed source text")

    monkeypatch.setattr(coordinator.providers, "extract", forbidden)
    monkeypatch.setattr(coordinator.providers, "embeddings", forbidden)
    receipt = await OwnerOperations(queue).submit(
        owner,
        BackupCommand(
            kg_id=source.kg_id,
            expected_published_revision=1,
            idempotency_key="sealed-source-backup",
        ),
    )
    job = await queue.claim("backup-worker", kinds=("backup",))
    service = BackupService(queue)
    await JobRunner(
        queue, configured_pipeline(queue, coordinator.providers), operations=service
    ).run_job(job)
    status = await queue.get(owner, receipt.job_id)
    assert status.stage == "ready", status.warnings
    manifest = await service.manifest(owner, source.kg_id, receipt.job_id)
    assert manifest.backup_id == job.target_id and manifest.snapshot.revision == 1
    assert manifest.source_count == 1 and manifest.curation_count == 0
    page = await service.parts(owner, source.kg_id, manifest.backup_id, 0)
    assert {part.kind for part in page.parts} >= {"authority", "original", "source_projection"}
    original = next(part for part in page.parts if part.kind == "original")
    assert original.source.material_id == source.material_id
    binding = (await queue.router.for_sources(owner, source.kg_id)).blobs
    store = PackageStore(binding)
    assert b"".join([chunk async for chunk in store.open(original.object)]) == TEXT.encode()
    assert await service.verify(owner, source.kg_id, manifest.backup_id) == manifest
    await queue.finish(job)  # A lost worker response replays the sealed result.
    assert await queue.accepted(job)
    # Every nested reference needed to validate a restored attempt is retained.
    from domain_kg.operations.backup_models import AuthoritySection

    sections = [
        AuthoritySection.model_validate_json(
            b"".join([chunk async for chunk in store.open(part.object)])
        )
        for part in page.parts
        if part.kind == "authority"
    ]
    assert "projection_record_refs" in {section.table for section in sections}
    assert "kg_schemas" in {section.table for section in sections}
    attempts = next(section for section in sections if section.table == "projection_attempts")
    assert "lease" not in attempts.records[0]
    assert not {"lease_token", "lease_until", "fence"}.intersection(attempts.records[0]["job"])
    assert not await queue.database.read_pins.find_one(
        {
            "org_id": str(owner.org_id),
            "kg_id": str(source.kg_id),
            "kind": "backup_capture",
            "job_id": str(job.id),
        }
    )
    reader = await queue.policy.resolve_access(team[2]["reader"], [source.kg_id], Action.READ)
    with pytest.raises(DomainError):
        await service.manifest(reader, source.kg_id, receipt.job_id)
    binding.client.put_object(
        Bucket=binding.bucket, Key=store._key(original.object), Body=b"damaged retained original"
    )
    with pytest.raises(DomainError):
        await service.verify(owner, source.kg_id, manifest.backup_id)
    monkeypatch.setattr("domain_kg.operations.backup.now", lambda: manifest.expires_at)
    with pytest.raises(DomainError) as expired:
        await service.manifest(owner, source.kg_id, receipt.job_id)
    assert expired.value.code == ErrorCode.RESOURCE_NOT_ACCESSIBLE


async def test_backup_keeps_manual_history_and_withdrawal_without_model_calls(
    publishing, team, monkeypatch
):
    from test_curation import alpha_id

    from domain_kg.materials.lifecycle import MaterialLifecycle
    from domain_kg.materials.models import RemoveMaterial
    from domain_kg.operations.backup import BackupService
    from domain_kg.operations.backup_models import AuthoritySection
    from domain_kg.revisions.models import NodeChanges, NodeUpdate
    from domain_kg.revisions.service import RevisionService

    materials, editor, queue, coordinator, _, _ = publishing
    source, source_job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, source_job, checkpoint))
    alpha = await alpha_id(queue, editor, source.kg_id)
    receipt = await RevisionService(queue, coordinator.providers).update_node(
        editor,
        NodeUpdate(
            kg_id=source.kg_id,
            entity_id=alpha,
            expected_revision=0,
            source_ids=(source.material_id,),
            changes=NodeChanges(tags=("reviewed",)),
            reason="Preserve human history",
            idempotency_key="backup-curation-tags",
        ),
    )
    pipeline = configured_pipeline(queue, coordinator.providers)
    curation_job = await queue.claim("curation-before-backup", kinds=("curate",))
    await JobRunner(queue, pipeline).run_job(curation_job)
    assert (await queue.get(editor, receipt.job_id)).stage == "ready"
    await MaterialLifecycle(materials, queue).remove(
        editor,
        RemoveMaterial(
            kg_id=source.kg_id,
            material_id=source.material_id,
            expected_revision=1,
            action="delete",
            idempotency_key="backup-retained-withdrawal",
        ),
    )
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.BACKUP)

    async def forbidden(*args, **kwargs):
        pytest.fail("retained capture must not call a model")

    monkeypatch.setattr(coordinator.providers, "extract", forbidden)
    monkeypatch.setattr(coordinator.providers, "embeddings", forbidden)
    await OwnerOperations(queue).submit(
        owner,
        BackupCommand(
            kg_id=source.kg_id,
            expected_published_revision=2,
            idempotency_key="backup-curation-history",
        ),
    )
    job = await queue.claim("backup-curation", kinds=("backup",))
    service = BackupService(queue)
    await JobRunner(queue, pipeline, operations=service).run_job(job)
    assert (await queue.get(owner, job.id)).stage == "ready"
    manifest = await service.verify(owner, source.kg_id, job.target_id)
    assert manifest.curation_count == 1
    store = PackageStore((await queue.router.for_sources(owner, source.kg_id)).blobs)
    sections = []
    curations = []
    for index in range(len(manifest.pages)):
        page = await service.parts(owner, source.kg_id, job.target_id, index)
        for part in page.parts:
            if part.kind == "authority":
                sections.append(
                    AuthoritySection.model_validate_json(
                        b"".join([chunk async for chunk in store.open(part.object)])
                    )
                )
            elif part.kind == "curation_projection":
                curations.append(part)
    assert len(curations) == 1 and curations[0].curation.id == curation_job.target_id
    access = next(section for section in sections if section.table == "material_access")
    assert access.records[0]["tombstoned"] is True
    history = next(section for section in sections if section.table == "curation_revisions")
    assert history.records[0]["provenance"]["reason"] == "Preserve human history"


@pytest.mark.parametrize("damage", ["generation", "source"])
async def test_backup_rejects_inconsistent_accepted_projection(publishing, team, damage):
    from domain_kg.operations.backup import BackupService

    _, editor, queue, coordinator, _, _ = publishing
    source, source_job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, source_job, checkpoint))
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.BACKUP)
    await OwnerOperations(queue).submit(
        owner,
        BackupCommand(
            kg_id=source.kg_id, expected_published_revision=1, idempotency_key="bad-backup-binding"
        ),
    )
    job = await queue.claim("backup-worker", kinds=("backup",))
    field = "batch.generation" if damage == "generation" else "source.material_id"
    changed = await queue.database.projection_attempts.update_one(
        {"org_id": str(owner.org_id), "kg_id": str(source.kg_id), "job.id": str(source_job.id)},
        {"$set": {field: str(uuid4())}},
    )
    assert changed.matched_count == 1
    with pytest.raises(DomainError) as invalid:
        await BackupService(queue).run(job)
    assert invalid.value.code == ErrorCode.PROJECTION_INCONSISTENT
    assert not await queue.database.backup_results.find_one({"id": str(job.target_id)})


@pytest.mark.parametrize(
    "race", ["withdrawal", "revocation", "retained_corruption", "configuration", "publication"]
)
async def test_backup_cannot_seal_after_capture_authority_changes(
    publishing, team, monkeypatch, race
):
    from domain_kg.materials.lifecycle import MaterialLifecycle
    from domain_kg.materials.models import RemoveMaterial
    from domain_kg.operations.backup import BackupService

    materials, editor, queue, coordinator, _, _ = publishing
    source, source_job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, source_job, checkpoint))
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.BACKUP)
    await OwnerOperations(queue).submit(
        owner,
        BackupCommand(
            kg_id=source.kg_id, expected_published_revision=1, idempotency_key="backup-seal-race"
        ),
    )
    job = await queue.claim("backup-worker", kinds=("backup",))
    service = BackupService(queue)
    seal = service._seal

    async def change(context, manifest):
        if race == "withdrawal":
            await MaterialLifecycle(materials, queue).remove(
                editor,
                RemoveMaterial(
                    kg_id=source.kg_id,
                    material_id=source.material_id,
                    action="delete",
                    expected_revision=1,
                    idempotency_key="during-backup-seal",
                ),
            )
        elif race == "revocation":
            await queue.database.members.update_one(
                {"org_id": str(owner.org_id), "id": str(team[3]["owner"].id)},
                {"$set": {"suspended": True}},
            )
        elif race == "configuration":
            await queue.database.kgs.update_one(
                {"org_id": str(owner.org_id), "id": str(source.kg_id)},
                {"$inc": {"configuration_epoch": 1}},
            )
        elif race == "publication":
            _, next_job, checkpoint = await prepare(publishing, key="publish-during-backup")
            await coordinator.publish(editor, await ready(publishing, next_job, checkpoint))
        else:
            from domain_kg.operations.packages import PackageObject

            raw = await queue.database.backup_objects.find_one({"job_id": str(job.id)})
            reference = PackageObject.model_validate(
                {key: raw[key] for key in PackageObject.model_fields}
            )
            context.packages.binding.client.put_object(
                Bucket=context.packages.binding.bucket,
                Key=context.packages._key(reference),
                Body=b"corrupted synthetic retained object",
            )
        await seal(context, manifest)

    monkeypatch.setattr(service, "_seal", change)
    with pytest.raises(DomainError):
        await service.run(job)
    assert not await queue.database.backup_results.find_one({"id": str(job.target_id)})
    assert not await queue.database.read_pins.find_one({"job_id": str(job.id)})


async def test_backup_requires_all_selected_originals(publishing, team):
    from domain_kg.operations.backup import BackupService

    _, editor, queue, coordinator, _, _ = publishing
    source, source_job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, source_job, checkpoint))
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.BACKUP)
    await OwnerOperations(queue).submit(
        owner,
        BackupCommand(
            kg_id=source.kg_id,
            expected_published_revision=1,
            idempotency_key="missing-backup-original",
        ),
    )
    job = await queue.claim("backup-worker", kinds=("backup",))
    stores = await queue.router.for_sources(owner, source.kg_id)
    await stores.authority._database.version_blobs.delete_one(
        {"org_id": str(owner.org_id), "kg_id": str(source.kg_id), "id": str(source.version_id)}
    )
    with pytest.raises(DomainError) as missing:
        await BackupService(queue).run(job)
    assert missing.value.code == ErrorCode.PROJECTION_INCONSISTENT
    assert not await queue.database.backup_results.find_one({"id": str(job.target_id)})
