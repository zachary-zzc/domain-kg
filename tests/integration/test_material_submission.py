"""Accepted originals and durable jobs commit atomically; projections are later work."""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from domain_kg.errors import DomainError
from domain_kg.identity.models import Action
from domain_kg.materials.models import AddMaterial, ReplaceMaterial

pytestmark = pytest.mark.storage


async def test_upload_ownership_idempotency_acl_and_original_download(
    material_service, team, mongo_db
) -> None:
    from domain_kg.materials.models import UploadCommand

    service, kg = material_service
    _, policy, principals, _, org, _ = team

    async def access(name, action=Action.INGEST):
        return await policy.resolve_access(principals[name], [kg], action)

    data = b"Private original\n"

    async def stream():
        yield data

    upload = await service.upload(
        await access("editor"),
        UploadCommand(
            kg_id=kg,
            media_type="text/plain",
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            idempotency_key="upload",
        ),
        stream(),
    )
    command = AddMaterial(
        kg_id=kg,
        upload_id=upload.upload_id,
        title="Restricted",
        acl_mode="restricted",
        idempotency_key="add",
    )
    with pytest.raises(DomainError):
        await service.add(await access("owner"), command)
    receipt = await service.add(await access("editor"), command)
    assert await service.add(await access("editor"), command) == receipt
    assert await mongo_db.jobs.count_documents({"org_id": str(org), "kg_id": str(kg)}) == 1
    assert (
        await mongo_db.material_versions.count_documents({"org_id": str(org), "kg_id": str(kg)})
        == 1
    )
    original = b"".join(
        [
            chunk
            async for chunk in service.original(
                await access("editor", Action.READ), kg, receipt.material_id, receipt.version_id
            )
        ]
    )
    assert original == data
    with pytest.raises(DomainError):
        _ = b"".join(
            [
                chunk
                async for chunk in service.original(
                    await access("reader", Action.READ), kg, receipt.material_id, receipt.version_id
                )
            ]
        )
    public = await service.add(
        await access("editor"),
        AddMaterial(kg_id=kg, inline_text=data.decode(), title="Public", idempotency_key="public"),
    )
    first = await mongo_db.version_blobs.find_one(
        {"org_id": str(org), "kg_id": str(kg), "id": str(receipt.version_id)}
    )
    second = await mongo_db.version_blobs.find_one(
        {"org_id": str(org), "kg_id": str(kg), "id": str(public.version_id)}
    )
    assert first["blob"]["sha256"] == second["blob"]["sha256"]
    assert first["blob"]["object_id"] != second["blob"]["object_id"]
    with pytest.raises(DomainError):
        await service.add(await access("editor"), command.model_copy(update={"title": "Different"}))


async def test_replace_fences_expected_active_version_and_preserves_original(
    material_service, team, mongo_db
) -> None:
    service, kg = material_service
    _, policy, principals, _, org, _ = team
    access = await policy.resolve_access(principals["editor"], [kg], Action.INGEST)
    first = await service.add(
        access,
        AddMaterial(kg_id=kg, inline_text="Version one", title="Original", idempotency_key="first"),
    )
    # Task 10 owns real publication. Establish a retained accepted source fixture.
    await mongo_db.material_access.update_one(
        {"org_id": str(org), "kg_id": str(kg), "id": str(first.material_id)},
        {"$set": {"active_version_id": str(first.version_id)}},
    )
    await mongo_db.version_blobs.update_one(
        {"org_id": str(org), "kg_id": str(kg), "id": str(first.version_id)},
        {"$set": {"state": "accepted"}},
    )
    replace = await policy.resolve_access(principals["editor"], [kg], Action.REPLACE)
    with pytest.raises(DomainError) as conflict:
        await service.replace(
            replace,
            ReplaceMaterial(
                kg_id=kg,
                material_id=first.material_id,
                expected_active_version=uuid4(),
                inline_text="Bad",
                title="New",
                idempotency_key="bad-version",
            ),
        )
    assert conflict.value.code == "VERSION_CONFLICT"
    second = await service.replace(
        replace,
        ReplaceMaterial(
            kg_id=kg,
            material_id=first.material_id,
            expected_active_version=first.version_id,
            inline_text="Version two",
            title="New",
            idempotency_key="second",
        ),
    )
    assert second.material_id == first.material_id and second.version_id != first.version_id
    active = await mongo_db.material_access.find_one(
        {"org_id": str(org), "kg_id": str(kg), "id": str(first.material_id)}
    )
    assert active["active_version_id"] == str(first.version_id)
    assert (
        b"".join(
            [
                chunk
                async for chunk in service.original(
                    await policy.resolve_access(principals["reader"], [kg], Action.READ),
                    kg,
                    first.material_id,
                    first.version_id,
                )
            ]
        )
        == b"Version one"
    )


async def test_quota_reservation_and_expired_unclaimed_upload(
    material_service, team, mongo_db
) -> None:
    from domain_kg.materials.models import UploadCommand

    service, kg = material_service
    _, policy, principals, _, org, _ = team
    access = await policy.resolve_access(principals["editor"], [kg], Action.INGEST)

    async def stream():
        yield b"one"

    upload = await service.upload(
        access,
        UploadCommand(
            kg_id=kg,
            media_type="text/plain",
            sha256=hashlib.sha256(b"one").hexdigest(),
            size_bytes=3,
            idempotency_key="expiring",
        ),
        stream(),
    )
    await mongo_db.uploads.update_one(
        {"org_id": str(org), "kg_id": str(kg), "id": str(upload.upload_id)},
        {"$set": {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}},
    )
    with pytest.raises(DomainError):
        await service.add(
            access,
            AddMaterial(
                kg_id=kg, upload_id=upload.upload_id, title="Expired", idempotency_key="expired"
            ),
        )
    await service.expire_uploads(access)
    budget = await mongo_db.kg_quotas.find_one({"org_id": str(org), "kg_id": str(kg)})
    assert budget["reserved_bytes"] == 0
    await mongo_db.kg_quotas.update_one(
        {"org_id": str(org), "kg_id": str(kg)}, {"$set": {"limit_bytes": 1}}
    )
    with pytest.raises(DomainError) as quota:
        await service.upload(
            access,
            UploadCommand(
                kg_id=kg,
                media_type="text/plain",
                sha256=hashlib.sha256(b"one").hexdigest(),
                size_bytes=3,
                idempotency_key="over-budget",
            ),
            stream(),
        )
    assert quota.value.code == "QUOTA_EXCEEDED"


async def test_concurrent_upload_replay_cannot_release_another_streams_quota(
    material_service, team
):
    import asyncio

    from domain_kg.materials.models import UploadCommand

    service, kg = material_service
    _, policy, principals, _, _, _ = team
    access = await policy.resolve_access(principals["editor"], [kg], Action.INGEST)
    command = UploadCommand(
        kg_id=kg,
        media_type="text/plain",
        sha256=hashlib.sha256(b"one").hexdigest(),
        size_bytes=3,
        idempotency_key="concurrent-upload",
    )
    started, resume = asyncio.Event(), asyncio.Event()

    async def first_stream():
        started.set()
        await resume.wait()
        yield b"one"

    async def bad_stream():
        yield b"bad"

    first = asyncio.create_task(service.upload(access, command, first_stream()))
    await started.wait()
    try:
        with pytest.raises(DomainError) as busy:
            await service.upload(access, command, bad_stream())
        assert busy.value.code == "REVISION_NOT_READY"
    finally:
        resume.set()
        result = await first
    assert result.size_bytes == 3
    assert await service.upload(access, command, bad_stream()) == result


async def test_current_acl_is_checked_between_original_chunks(material_service, team):
    service, kg = material_service
    _, policy, principals, members, _, _ = team
    access = await policy.resolve_access(principals["editor"], [kg], Action.INGEST)
    receipt = await service.add(
        access,
        AddMaterial(kg_id=kg, inline_text="x" * 140000, title="Stream", idempotency_key="stream"),
    )
    stream = service.original(
        await policy.resolve_access(principals["editor"], [kg], Action.READ),
        kg,
        receipt.material_id,
        receipt.version_id,
    )
    assert len(await anext(stream)) == 65536
    owner = await policy.resolve_access(principals["owner"], [kg], Action.MANAGE_GRANTS)
    await policy.set_material_acl(
        owner,
        kg,
        receipt.material_id,
        "restricted",
        member_ids=(),
        group_ids=(),
        idempotency_key="restrict",
    )
    with pytest.raises(DomainError):
        await anext(stream)
    await stream.aclose()


async def test_cross_database_acceptance_rolls_back_when_outbox_fails(
    material_service, team, mongo_db, monkeypatch
):
    from dataclasses import replace

    from pymongo.asynchronous.collection import AsyncCollection

    from domain_kg.storage.integrity import NamespaceRepository, install_mongo_schema

    service, kg = material_service
    _, policy, principals, _, org, _ = team
    source_db = mongo_db.client[mongo_db.name + "_sources"]
    await install_mongo_schema(source_db)
    route = service.router.for_kg

    async def cross_database(access, kg_id):
        stores = await route(access, kg_id)
        return replace(stores, authority=NamespaceRepository(source_db, stores.namespace))

    monkeypatch.setattr(service.router, "for_kg", cross_database)
    insert_one = AsyncCollection.insert_one

    async def fail_outbox(collection, *args, **kwargs):
        if collection.name == "outbox":
            raise DomainError("BACKEND_UNAVAILABLE", "Injected outbox failure.")
        return await insert_one(collection, *args, **kwargs)

    access = await policy.resolve_access(principals["editor"], [kg], Action.INGEST)
    command = AddMaterial(
        kg_id=kg, inline_text="Transactional", title="Atomic", idempotency_key="atomic"
    )
    try:
        with monkeypatch.context() as patch:
            patch.setattr(AsyncCollection, "insert_one", fail_outbox)
            with pytest.raises(DomainError):
                await service.add(access, command)
        ns = {"org_id": str(org), "kg_id": str(kg)}
        assert await source_db.materials.count_documents(ns) == 0
        assert await source_db.material_versions.count_documents(ns) == 0
        assert await mongo_db.jobs.count_documents(ns) == 0
        assert await mongo_db.material_access.count_documents(ns) == 0
        budget = await mongo_db.kg_quotas.find_one(ns)
        assert budget["reserved_bytes"] == len(b"Transactional") and budget["committed_bytes"] == 0
        result = await service.add(access, command)
        assert await source_db.material_versions.count_documents(ns) == 1
        assert (
            b"".join(
                [
                    part
                    async for part in service.original(
                        await policy.resolve_access(principals["editor"], [kg], Action.READ),
                        kg,
                        result.material_id,
                        result.version_id,
                    )
                ]
            )
            == b"Transactional"
        )
    finally:
        await mongo_db.client.drop_database(source_db.name)
