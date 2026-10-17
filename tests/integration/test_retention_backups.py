"""Corrupt expired backup content must stop before the first deletion."""
# ruff: noqa: F811

import asyncio
from datetime import timedelta

import pytest
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError
from domain_kg.identity.models import Action
from domain_kg.jobs.queue import now
from domain_kg.operations.backup import BackupService
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import BackupCommand, RetentionCommand
from domain_kg.operations.retention import RetentionService
from domain_kg.operations.storage import PackageStore

pytestmark = pytest.mark.storage


async def test_backup_cleanup_verifies_all_parts_before_erasing_and_preserves_live_originals(
    publishing, team, monkeypatch
):
    _, editor, queue, coordinator, _, _ = publishing
    source, source_job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, source_job, checkpoint))
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.CONFIGURE)
    operations = OwnerOperations(queue)
    past = now() - timedelta(days=10)
    monkeypatch.setattr("domain_kg.operations.backup.now", lambda: past)
    await operations.submit(
        owner,
        BackupCommand(
            kg_id=source.kg_id, expected_published_revision=1, idempotency_key="retained-backup"
        ),
    )
    backup_job = await queue.claim("backup", kinds=("backup",))
    await BackupService(queue).run(backup_job)
    from domain_kg.operations.backup_models import BackupManifest, BackupSeal
    from domain_kg.operations.package_reader import PackageReader

    raw = await queue.database.backup_results.find_one({"id": str(backup_job.target_id)})
    seal = BackupSeal.model_validate({key: raw[key] for key in BackupSeal.model_fields})
    binding = (await queue.router.for_sources(owner, source.kg_id)).blobs
    store = PackageStore(binding)

    async def guard():
        await queue.policy.revalidate(owner, action=Action.CONFIGURE)

    reader = PackageReader(store, guard)
    manifest = BackupManifest.model_validate_json(await reader.body(seal.manifest))
    pages = [page async for page in reader.pages(manifest)]
    original = next(part.object for page in pages for part in page.parts if part.kind == "original")
    content = b"".join([chunk async for chunk in store.open(original)])
    await asyncio.to_thread(
        binding.client.put_object, Bucket=binding.bucket, Key=store._key(original), Body=b"corrupt"
    )
    await operations.submit(
        owner,
        RetentionCommand(
            kg_id=source.kg_id,
            expected_published_revision=1,
            resources="backups",
            idempotency_key="erase-backup",
        ),
    )
    job = await queue.claim("retention", kinds=("retention",))
    service = RetentionService(queue)
    with pytest.raises(DomainError):
        await service.run(job)
    assert b"".join([chunk async for chunk in store.open(seal.manifest)])
    assert not await queue.database.retention_inventories.find_one(
        {"cleanup_job_id": str(job.id), "sealed": True}
    )
    assert not await queue.database.retention_results.find_one({"id": str(job.target_id)})
    await asyncio.to_thread(
        binding.client.put_object, Bucket=binding.bucket, Key=store._key(original), Body=content
    )
    await service.run(job)
    assert await queue.accepted(job)
    for reference in (original, seal.manifest):
        with pytest.raises(DomainError):
            await anext(store.open(reference))
    assert await queue.database.material_versions.find_one({"id": str(source.version_id)})
    assert (await queue.database.kgs.find_one({"id": str(source.kg_id)}))["published_revision"] == 1
