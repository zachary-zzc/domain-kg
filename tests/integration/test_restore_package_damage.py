"""A damaged retained backup fails before allocating target storage or copying data."""
# ruff: noqa: F811

import asyncio
from unittest.mock import AsyncMock

import pytest
from test_entity_resolution import TEXT
from test_publication import publishing  # noqa: F401
from test_restore_preparation import setup_restore

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.operations.backup import BackupService
from domain_kg.operations.restore_preparation import RestorePreparation
from domain_kg.operations.storage import PackageStore
from domain_kg.storage.blobs import BlobStore

pytestmark = pytest.mark.storage


async def test_corrupt_retained_original_cannot_write_or_activate_target(
    publishing, team, catalog, monkeypatch
):
    source, owner, job, target, backup = await setup_restore(publishing, team, catalog)
    _, _, queue, coordinator, _, _ = publishing
    service = BackupService(queue)
    manifest = await service.manifest(owner, source.kg_id, backup.job_id)
    page = await service.parts(owner, source.kg_id, manifest.backup_id, 0)
    part = next(p for p in page.parts if p.kind == "original")
    source_stores = await queue.router.for_sources(owner, source.kg_id)
    store = PackageStore(source_stores.blobs)
    await asyncio.to_thread(
        source_stores.blobs.client.put_object,
        Bucket=source_stores.blobs.bucket,
        Key=store._key(part.object),
        Body=b"damaged fixture",
    )
    copy = AsyncMock(side_effect=AssertionError("damaged package must fail before target writes"))
    monkeypatch.setattr(RestorePreparation, "_put", copy)
    with pytest.raises(DomainError) as corrupt:
        await RestorePreparation(queue, coordinator.providers).prepare(job)
    assert corrupt.value.code in (
        ErrorCode.EVIDENCE_VALIDATION_FAILED,
        ErrorCode.PROJECTION_INCONSISTENT,
    )
    copy.assert_not_called()
    collection = f"kg_{target.target.org_id.hex}_{target.target.kg_id.hex}_{target.generation.hex}"
    assert not await queue.router._vectors[target.placement.qdrant_id].collection_exists(collection)
    assert await queue.database.kgs.find_one({"id": str(target.target.kg_id)}) is None
    assert not await queue.accepted(job)
    body = b"".join([chunk async for chunk in BlobStore(source_stores.blobs).open(part.original)])
    assert body == TEXT.encode()
