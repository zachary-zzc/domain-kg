"""Tiny versioned S3 fixture: erase one retained object, preserve every sibling."""

import asyncio
import hashlib
from uuid import uuid4

import pytest

from domain_kg.identity.models import Action
from domain_kg.operations.package_erasure import ErasureLimits, PackageErasure
from domain_kg.operations.packages import PackageObject
from domain_kg.operations.storage import PackageStore

pytestmark = pytest.mark.storage


async def test_exact_retained_key_versions_and_uploads_are_erased_without_touching_live_data(
    material_service, team
):
    materials, kg = material_service
    owner = await team[1].resolve_access(team[2]["owner"], [kg], Action.CONFIGURE)
    binding = (await materials.router.for_sources(owner, kg)).blobs
    store = PackageStore(binding)
    client, bucket = binding.client, binding.bucket
    await asyncio.to_thread(
        client.put_bucket_versioning, Bucket=bucket, VersioningConfiguration={"Status": "Enabled"}
    )
    body = b"tiny"
    reference = PackageObject(
        org_id=owner.org_id,
        kg_id=kg,
        backup_id=uuid4(),
        attempt_id=uuid4(),
        id=uuid4(),
        sha256=hashlib.sha256(body).hexdigest(),
        size_bytes=len(body),
    )
    key = store._key(reference)
    sibling, live = key + "/keep", f"{owner.org_id}/{kg}/original/keep"
    for target, content in (
        (key, b"old"),
        (key, b"previous"),
        (sibling, b"neighbor"),
        (live, b"live"),
    ):
        await asyncio.to_thread(client.put_object, Bucket=bucket, Key=target, Body=content)
    await asyncio.to_thread(client.delete_object, Bucket=bucket, Key=key)

    async def stream():
        yield body

    await store.put(reference, stream())
    await asyncio.to_thread(client.create_multipart_upload, Bucket=bucket, Key=key)
    foreign = await asyncio.to_thread(client.create_multipart_upload, Bucket=bucket, Key=sibling)

    async def guard():
        await team[1].revalidate(owner, action=Action.CONFIGURE)

    page = await asyncio.to_thread(
        client.list_multipart_uploads, Bucket=bucket, Prefix=key, MaxUploads=1
    )
    assert page.get("IsTruncated") is True
    erasure = PackageErasure(store, authorize=guard, limits=ErasureLimits(page_size=1))
    await erasure.erase(reference)
    await erasure.erase(reference)
    versions = await asyncio.to_thread(client.list_object_versions, Bucket=bucket, Prefix=key)
    assert all(
        row["Key"] != key
        for field in ("Versions", "DeleteMarkers")
        for row in versions.get(field, [])
    )
    uploads = await asyncio.to_thread(client.list_multipart_uploads, Bucket=bucket, Prefix=key)
    assert [(row["Key"], row["UploadId"]) for row in uploads.get("Uploads", [])] == [
        (sibling, foreign["UploadId"])
    ]
    for target, expected in ((sibling, b"neighbor"), (live, b"live")):
        result = await asyncio.to_thread(client.get_object, Bucket=bucket, Key=target)
        try:
            assert await asyncio.to_thread(result["Body"].read) == expected
        finally:
            await asyncio.to_thread(result["Body"].close)
    await asyncio.to_thread(
        client.abort_multipart_upload, Bucket=bucket, Key=sibling, UploadId=foreign["UploadId"]
    )
