"""A sealed package prepares real target bytes while the new KG remains inaccessible."""
# ruff: noqa: F811

import pytest
from test_entity_resolution import TEXT
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.operations.backup import BackupService
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import BackupCommand, RestoreCommand
from domain_kg.operations.restore_targets import RestoreTargets
from domain_kg.storage.blobs import BlobStore

pytestmark = pytest.mark.storage


async def setup_restore(publishing, team, catalog):
    _, editor, queue, coordinator, _, _ = publishing
    source, native, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, native, checkpoint))
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.RESTORE)
    operations = OwnerOperations(queue)
    backup = await operations.submit(
        owner,
        BackupCommand(
            kg_id=source.kg_id,
            expected_published_revision=1,
            idempotency_key="prepare-source-backup",
        ),
    )
    await BackupService(queue).run(await queue.claim("capture", kinds=("backup",)))
    registry = coordinator.providers.registry
    profile = next(iter(registry.profiles.values()))
    receipt = await operations.submit(
        owner,
        RestoreCommand(
            kg_id=source.kg_id,
            expected_published_revision=1,
            backup_job_id=backup.job_id,
            placement_id=catalog[3].id,
            provider_profile_id=profile.id,
            name="Prepared recovery",
            idempotency_key="prepare-source-restore",
        ),
    )
    job = await queue.claim("prepare-restore", kinds=("restore",))
    target = await RestoreTargets(queue).get(owner, source.kg_id, receipt.job_id)
    registry.profiles[profile.id] = profile.model_copy(
        update={
            "allowed_namespaces": (*profile.allowed_namespaces, target.target),
            "allow_restricted": True,
        }
    )
    return source, owner, job, target, backup


async def test_prepare_copies_verified_originals_and_reseals_actual_native_artifacts(
    publishing, team, catalog, monkeypatch
):
    from domain_kg.operations.restore_preparation import RestorePreparation

    source, owner, job, target, _ = await setup_restore(publishing, team, catalog)
    _, _, queue, coordinator, _, _ = publishing
    staged = None

    async def forbidden(*args, **kwargs):
        pytest.fail("unchanged native restore preparation must make no provider call")

    monkeypatch.setattr(coordinator.providers, "embeddings", forbidden)
    monkeypatch.setattr(coordinator.providers, "extract", forbidden)
    try:
        staged = await RestorePreparation(queue, coordinator.providers).prepare(job)
        assert staged.binding.target == target
        assert len(staged.originals) == 1 and len(staged.sources) == 1
        original = staged.originals[0]
        assert original.kg_id == target.target.kg_id
        data = b"".join(
            [part async for part in BlobStore(staged.binding.stores.blobs).open(original)]
        )
        assert data == TEXT.encode()
        assert len(staged.artifacts) >= 5
        assert {item.checkpoint.stage for item in staged.artifacts} >= {
            "validating",
            "parsing",
            "chunking",
            "extracting",
            "resolving",
        }
        for item in staged.artifacts:
            stored = b"".join(
                [
                    part
                    async for part in BlobStore(staged.binding.stores.blobs).open(
                        item.artifact.blob
                    )
                ]
            )
            assert stored == item.body
        replay = await RestorePreparation(queue, coordinator.providers).prepare(job)
        assert replay.originals == staged.originals and replay.artifacts == staged.artifacts
        from domain_kg.operations.restore_authority import RestoreAuthority

        from domain_kg.models import ScopedRef

        await RestoreAuthority(queue, coordinator.providers).stage(job, staged)
        await RestoreAuthority(queue, coordinator.providers).stage(job, staged)
        assert await staged.binding.stores.authority.reconcile() == []
        version = await staged.binding.stores.authority.get_material_version(
            ScopedRef(**target.target.model_dump(), id=source.version_id)
        )
        assert version.material.id == source.material_id
        document = staged.sources[0].document
        assert document.kg_id == target.target.kg_id and document.material_id == source.material_id
        assert all(row.source.kg_id == target.target.kg_id for row in document.rows)
        assert all(row.text for row in document.rows)
        assert await queue.database.kgs.find_one({"id": str(target.target.kg_id)}) is None
        with pytest.raises(DomainError):
            await queue.policy.resolve_access(team[2]["owner"], [target.target.kg_id], Action.READ)
        with pytest.raises(DomainError) as incomplete:
            await queue.finish(job)
        assert incomplete.value.code == ErrorCode.REVISION_NOT_READY
    finally:
        client = queue.router._vectors.get(target.placement.qdrant_id)
        collection = (
            f"kg_{target.target.org_id.hex}_{target.target.kg_id.hex}_{target.generation.hex}"
        )
        if client is not None and await client.collection_exists(collection):
            await client.delete_collection(collection)
