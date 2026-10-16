"""Streaming originals, namespace ownership and real S3 integrity checks."""

import hashlib
from uuid import uuid4

import pytest

from domain_kg.errors import DomainError
from domain_kg.materials.models import BlobScope

pytestmark = pytest.mark.storage


async def parts(data):
    for offset in range(0, len(data), 65536):
        yield data[offset : offset + 65536]


async def test_s3_original_hash_namespace_and_immutable_key(blob_store, monkeypatch) -> None:
    blobs, kg, org = blob_store
    scope = BlobScope(org_id=org, kg_id=kg, material_id=uuid4(), version_id=uuid4())
    data = b"  original evidence\n"
    blob = await blobs.put(parts(data), hashlib.sha256(data).hexdigest(), scope)
    assert b"".join([chunk async for chunk in blobs.open(blob)]) == data
    monkeypatch.setattr("domain_kg.storage.blobs.uuid4", lambda: blob.object_id)
    with pytest.raises(DomainError):
        await blobs.put(parts(b"changed"), hashlib.sha256(b"changed").hexdigest(), scope)
    assert b"".join([chunk async for chunk in blobs.open(blob)]) == data
    with pytest.raises(DomainError):
        _ = b"".join(
            [chunk async for chunk in blobs.open(blob.model_copy(update={"kg_id": uuid4()}))]
        )
    with pytest.raises(DomainError):
        await blobs.put(parts(data), "0" * 64, scope)
    with pytest.raises(DomainError):
        await blobs.put(
            parts(data), hashlib.sha256(data).hexdigest(), scope, media_type="application/pdf"
        )


async def test_streamed_fifty_mib_boundary_without_large_ram_buffer(blob_store) -> None:
    blobs, kg, org = blob_store
    chunk = b"x" * 65536
    digest = hashlib.sha256()
    for _ in range(800):
        digest.update(chunk)

    async def stream(extra=False):
        for _ in range(800):
            yield chunk
        if extra:
            yield b"x"

    scope = BlobScope(org_id=org, kg_id=kg, material_id=uuid4(), version_id=uuid4())
    blob = await blobs.put(stream(), digest.hexdigest(), scope)
    assert blob.size_bytes == 50 * 1024 * 1024
    with pytest.raises(DomainError) as too_large:
        await blobs.put(stream(True), digest.hexdigest(), scope)
    assert too_large.value.code == "QUOTA_EXCEEDED"


async def test_multipart_completion_cannot_overwrite_an_original(blob_store, monkeypatch):
    blobs, kg, org = blob_store
    size = 6 * 1024 * 1024
    chunk = b"a" * 65536
    digest = hashlib.sha256()
    for _ in range(size // len(chunk)):
        digest.update(chunk)

    async def stream(value):
        for _ in range(size // len(chunk)):
            yield value

    scope = BlobScope(org_id=org, kg_id=kg, material_id=uuid4(), version_id=uuid4())
    blob = await blobs.put(stream(chunk), digest.hexdigest(), scope)
    monkeypatch.setattr("domain_kg.storage.blobs.uuid4", lambda: blob.object_id)
    other = b"b" * 65536
    changed = hashlib.sha256()
    for _ in range(size // len(other)):
        changed.update(other)
    with pytest.raises(DomainError):
        await blobs.put(stream(other), changed.hexdigest(), scope)
    checked = hashlib.sha256()
    async for part in blobs.open(blob):
        checked.update(part)
    assert checked.hexdigest() == digest.hexdigest()
