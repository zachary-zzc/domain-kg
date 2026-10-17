"""Retained objects use exact scoped references and immutable checksum-verified bytes."""

import hashlib
from uuid import uuid4

import pytest

from domain_kg.errors import DomainError
from domain_kg.identity.models import Action

pytestmark = pytest.mark.storage


async def test_retained_object_integrity_replay_and_separate_live_prefix(material_service, team):
    from domain_kg.operations.packages import PackageObject
    from domain_kg.operations.storage import PackageStore

    materials, kg = material_service
    owner = await team[1].resolve_access(team[2]["owner"], [kg], Action.BACKUP)
    binding = (await materials.router.for_sources(owner, kg)).blobs
    store = PackageStore(binding)
    body = b"Tiny retained original."
    reference = PackageObject(
        org_id=owner.org_id,
        kg_id=kg,
        backup_id=uuid4(),
        attempt_id=uuid4(),
        id=uuid4(),
        sha256=hashlib.sha256(body).hexdigest(),
        size_bytes=len(body),
        media_type="text/plain",
    )

    async def stream():
        yield body[:4]
        yield body[4:]

    await store.put(reference, stream())
    await store.put(reference, stream())
    assert b"".join([part async for part in store.open(reference)]) == body
    assert store._key(reference).startswith(f"retained-backups/{owner.org_id}/{kg}/")
    assert not store._key(reference).startswith(f"{owner.org_id}/{kg}/")
    with pytest.raises(DomainError):
        async for _ in store.open(reference.model_copy(update={"kg_id": uuid4()})):
            pass
    binding.client.put_object(Bucket=binding.bucket, Key=store._key(reference), Body=b"corrupt")
    with pytest.raises(DomainError):
        async for _ in store.open(reference):
            pytest.fail("corrupt retained bytes must not be yielded")
