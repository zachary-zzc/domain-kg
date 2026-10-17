"""Exact retained-object erasure preserves prefix siblings and stops on revoked authority."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.models import Namespace
from domain_kg.operations.packages import PackageObject
from domain_kg.operations.storage import PackageStore


class VersionedObjects:
    def __init__(self):
        self.versions = []
        self.uploads = []
        self.deleted = []
        self.aborted = []

    def list_object_versions(
        self, *, Bucket, Prefix, MaxKeys, KeyMarker=None, VersionIdMarker=None
    ):
        rows = sorted(row for row in self.versions if row[0].startswith(Prefix))
        if KeyMarker is not None:
            rows = [row for row in rows if row[:2] > (KeyMarker, VersionIdMarker)]
        page = rows[: min(2, MaxKeys)]
        result = {"IsTruncated": len(rows) > len(page), "Versions": [], "DeleteMarkers": []}
        for key, version, marker in page:
            result["DeleteMarkers" if marker else "Versions"].append(
                {"Key": key, "VersionId": version}
            )
        if result["IsTruncated"]:
            result.update(NextKeyMarker=page[-1][0], NextVersionIdMarker=page[-1][1])
        return result

    def list_multipart_uploads(
        self, *, Bucket, Prefix, MaxUploads, KeyMarker=None, UploadIdMarker=None
    ):
        rows = sorted(row for row in self.uploads if row[0].startswith(Prefix))
        if KeyMarker is not None:
            rows = [row for row in rows if row > (KeyMarker, UploadIdMarker)]
        page = rows[: min(2, MaxUploads)]
        result = {
            "IsTruncated": len(rows) > len(page),
            "Uploads": [{"Key": key, "UploadId": upload} for key, upload in page],
        }
        if result["IsTruncated"]:
            result.update(NextKeyMarker=page[-1][0], NextUploadIdMarker=page[-1][1])
        return result

    def delete_object(self, *, Bucket, Key, VersionId):
        self.deleted.append((Key, VersionId))
        self.versions = [row for row in self.versions if row[:2] != (Key, VersionId)]

    def abort_multipart_upload(self, *, Bucket, Key, UploadId):
        self.aborted.append((Key, UploadId))
        self.uploads.remove((Key, UploadId))

    def head_object(self, *, Bucket, Key):
        if any(row[0] == Key for row in self.versions):
            return {"ContentLength": 1}
        raise ClientError(
            {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
            "HeadObject",
        )


def fixture_object():
    namespace = Namespace(org_id=uuid4(), kg_id=uuid4())
    client = VersionedObjects()
    binding = SimpleNamespace(
        namespace=namespace,
        client=client,
        bucket="synthetic-retention",
        upload_slot=asyncio.Semaphore(1),
    )
    store = PackageStore(binding)
    reference = PackageObject(
        **namespace.model_dump(),
        id=uuid4(),
        backup_id=uuid4(),
        attempt_id=uuid4(),
        sha256="a" * 64,
        size_bytes=1,
    )
    return store, client, reference, store._key(reference)


async def test_erasure_handles_versions_markers_uploads_and_preserves_prefix_siblings():
    from domain_kg.operations.package_erasure import PackageErasure

    store, client, reference, key = fixture_object()
    client.versions = [
        (key, "1", False),
        (key, "2", True),
        (key, "3", False),
        (key + "/sibling", "4", False),
    ]
    client.uploads = [(key, "upload-a"), (key, "upload-b"), (key + "/sibling", "foreign-upload")]
    checks = []

    async def authorize():
        checks.append(True)

    erasure = PackageErasure(store, authorize=authorize)
    await erasure.erase(reference)
    assert set(client.deleted) == {(key, "1"), (key, "2"), (key, "3")}
    assert set(client.aborted) == {(key, "upload-a"), (key, "upload-b")}
    assert client.versions == [(key + "/sibling", "4", False)]
    assert client.uploads == [(key + "/sibling", "foreign-upload")]
    await erasure.erase(reference)  # physical deletion is idempotent
    assert len(client.deleted) == 3 and len(client.aborted) == 2 and len(checks) >= 5


async def test_erasure_denies_cross_kg_and_revocation_before_deletion():
    from domain_kg.operations.package_erasure import PackageErasure

    store, client, reference, key = fixture_object()
    client.versions = [(key, "1", False)]

    async def revoked():
        raise DomainError(ErrorCode.POLICY_CHANGED, "Synthetic withdrawal")

    erasure = PackageErasure(store, authorize=revoked)
    with pytest.raises(DomainError):
        await erasure.erase(reference.model_copy(update={"kg_id": uuid4()}))
    with pytest.raises(DomainError):
        await erasure.erase(reference)
    assert client.deleted == [] and client.aborted == []


async def test_revocation_stops_a_partial_erasure_and_retry_reconciles_absence():
    from domain_kg.operations.package_erasure import PackageErasure

    store, client, reference, key = fixture_object()
    client.versions = [(key, "1", False), (key, "2", False)]

    async def withdraw_after_one():
        if client.deleted:
            raise DomainError(ErrorCode.POLICY_CHANGED, "Synthetic mid-operation withdrawal")

    with pytest.raises(DomainError):
        await PackageErasure(store, authorize=withdraw_after_one).erase(reference)
    assert len(client.deleted) == 1 and len(client.versions) == 1

    async def authorized():
        return None

    await PackageErasure(store, authorize=authorized).erase(reference)
    assert client.versions == [] and len(client.deleted) == 2


async def test_inventory_budget_fails_before_the_first_delete():
    from domain_kg.operations.package_erasure import ErasureLimits, PackageErasure

    store, client, reference, key = fixture_object()
    client.versions = [(key, "1", False), (key, "2", False)]

    async def authorized():
        return None

    with pytest.raises(DomainError) as failed:
        await PackageErasure(
            store, authorize=authorized, limits=ErasureLimits(max_records=1)
        ).erase(reference)
    assert failed.value.code == ErrorCode.QUOTA_EXCEEDED and client.deleted == []


async def test_cancel_waits_for_the_inflight_delete_to_finish():
    import threading

    from domain_kg.operations.package_erasure import PackageErasure

    store, client, reference, key = fixture_object()
    client.versions = [(key, "1", False)]
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original = client.delete_object

    def slow_delete(**kwargs):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(2)
        return original(**kwargs)

    client.delete_object = slow_delete

    async def authorized():
        return None

    deleting = asyncio.create_task(PackageErasure(store, authorize=authorized).erase(reference))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        deleting.cancel()
        await asyncio.sleep(0)
        assert not deleting.done()
        deleting.cancel()
        done, _ = await asyncio.wait((deleting,), timeout=0.02)
        assert not done
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await deleting
    assert client.versions == []


async def test_empty_upload_marker_advances_by_key_and_preserves_sibling(monkeypatch):
    from domain_kg.operations.package_erasure import ErasureLimits, PackageErasure

    store, client, reference, key = fixture_object()
    client.uploads = [(key, "target"), (key + "/sibling", "keep")]
    original = client.list_multipart_uploads

    def key_marker_page(**kwargs):
        if kwargs.get("KeyMarker") and not kwargs.get("UploadIdMarker"):
            rows = [
                (k, upload)
                for k, upload in client.uploads
                if k > kwargs["KeyMarker"] and k.startswith(kwargs["Prefix"])
            ]
            return {
                "IsTruncated": False,
                "Uploads": [{"Key": k, "UploadId": upload} for k, upload in rows],
            }
        page = original(**kwargs)
        if page["IsTruncated"]:
            page["NextUploadIdMarker"] = ""
        return page

    monkeypatch.setattr(client, "list_multipart_uploads", key_marker_page)

    async def guard():
        return None

    await PackageErasure(store, authorize=guard, limits=ErasureLimits(page_size=1)).erase(reference)
    assert client.aborted == [(key, "target")]
    assert client.uploads == [(key + "/sibling", "keep")]


@pytest.mark.parametrize("cursor", ["missing", "id_only"])
async def test_compatible_upload_listing_without_next_markers_is_bounded(monkeypatch, cursor):
    from domain_kg.operations.package_erasure import ErasureLimits, PackageErasure

    store, client, reference, key = fixture_object()
    # SeaweedFS lists by upload ID; a foreign prefix sibling can precede this key.
    client.uploads = [(key, "z"), (key, "y"), (key + "/sibling", "a")]

    def seaweed_page(**kwargs):
        rows = sorted(client.uploads, key=lambda row: row[1])
        rows = [row for row in rows if row[0].startswith(kwargs["Prefix"])]
        if kwargs.get("KeyMarker"):
            rows = [row for row in rows if row[0] == kwargs["KeyMarker"]]
        if kwargs.get("UploadIdMarker"):
            rows = [row for row in rows if row[1] > kwargs["UploadIdMarker"]]
        page = rows[: kwargs["MaxUploads"]]
        result = {
            "IsTruncated": len(page) == kwargs["MaxUploads"],
            "Uploads": [{"Key": k, "UploadId": upload} for k, upload in page],
        }
        if page and cursor == "id_only":
            result["NextUploadIdMarker"] = page[-1][1]
        return result

    monkeypatch.setattr(client, "list_multipart_uploads", seaweed_page)

    async def guard():
        return None

    erasure = PackageErasure(store, authorize=guard, limits=ErasureLimits(page_size=1))
    if cursor == "missing":
        with pytest.raises(DomainError):
            await erasure.erase(reference)
        assert client.aborted == []
        return
    await erasure.erase(reference)
    assert client.aborted == [(key, "y"), (key, "z")]
    assert client.uploads == [(key + "/sibling", "a")]


async def test_nonadvancing_upload_listing_fails_before_any_delete(monkeypatch):
    from domain_kg.operations.package_erasure import ErasureLimits, PackageErasure

    store, client, reference, key = fixture_object()
    client.uploads = [(key, "a")]

    def repeated(**kwargs):
        return {
            "IsTruncated": True,
            "NextKeyMarker": key,
            "NextUploadIdMarker": "a",
            "Uploads": [{"Key": key, "UploadId": "a"}],
        }

    monkeypatch.setattr(client, "list_multipart_uploads", repeated)

    async def guard():
        return None

    with pytest.raises(DomainError):
        await PackageErasure(store, authorize=guard, limits=ErasureLimits(page_size=1)).erase(
            reference
        )
    assert client.deleted == [] and client.aborted == []
