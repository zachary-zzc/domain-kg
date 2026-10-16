"""Import planning binds a verified owned upload to current namespace and authority."""

import hashlib
from uuid import uuid4

import pytest

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.imports.models import ImportSource
from domain_kg.imports.service import Importer
from domain_kg.materials.models import UploadCommand

pytestmark = pytest.mark.storage
CSV = (
    b"entity1,entity1_type,relation,entity2,entity2_type,pdf_name,source\n"
    b"Alpha,concept,uses,Beta,concept,a.pdf,source A\n"
)


async def upload_csv(material_service, team, *, key="legacy-upload", data=CSV):
    materials, kg = material_service
    editor = await team[1].resolve_access(team[2]["editor"], [kg], Action.INGEST)

    async def body():
        yield data

    receipt = await materials.upload(
        editor,
        UploadCommand(
            kg_id=kg,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            media_type="text/csv",
            idempotency_key=key,
        ),
        body(),
    )
    return materials, editor, receipt


async def test_import_plan_is_scoped_replayable_unverified_and_does_not_publish(
    material_service, team
):
    materials, editor, upload = await upload_csv(material_service, team)
    service = Importer(materials)
    await service.initialize()
    source = ImportSource(
        kg_id=upload.kg_id, upload_id=upload.upload_id, format="csv", idempotency_key="legacy-plan"
    )
    plan = await service.plan(editor, source)
    assert (plan.org_id, plan.kg_id) == (editor.org_id, upload.kg_id)
    assert plan.entities == 2 and plan.assertions == 1
    assert plan.content_hash == upload.sha256 and plan.support_status == "imported_unverified"
    assert plan.policy_revision == editor.policy_revision and plan.published_revision == 0
    assert plan.schema_id and plan.expires_at
    assert await service.plan(editor, source) == plan
    assert not await materials.database.jobs.find_one({"kg_id": str(upload.kg_id)})
    assert not await materials.database.publication_bindings.find_one({"kg_id": str(upload.kg_id)})
    reader = await team[1].resolve_access(team[2]["reader"], [upload.kg_id], Action.READ)
    with pytest.raises(DomainError):
        await service.plan(reader, source)
    with pytest.raises(DomainError):
        await service.plan(editor, source.model_copy(update={"kg_id": uuid4()}))
    assert "source A" not in plan.model_dump_json()
    assert "object_id" not in plan.model_dump_json()


async def test_import_plan_rejects_changed_upload_keys_and_stale_authority(material_service, team):
    materials, editor, upload = await upload_csv(material_service, team)
    service = Importer(materials)
    await service.initialize()
    source = ImportSource(
        kg_id=upload.kg_id, upload_id=upload.upload_id, format="csv", idempotency_key="legacy-plan"
    )
    await service.plan(editor, source)
    _, _, other = await upload_csv(material_service, team, key="other-upload", data=CSV + b"\n")
    with pytest.raises(DomainError) as conflict:
        await service.plan(editor, source.model_copy(update={"upload_id": other.upload_id}))
    assert conflict.value.code == ErrorCode.IDEMPOTENCY_CONFLICT
    await materials.database.kgs.update_one(
        {"org_id": str(editor.org_id), "id": str(upload.kg_id)},
        {"$inc": {"visibility_revision": 1}},
    )
    with pytest.raises(DomainError) as changed:
        await service.plan(editor, source)
    assert changed.value.code == ErrorCode.VERSION_CONFLICT


async def test_import_plan_validates_blob_reference_before_reading_any_object(
    material_service, team
):
    materials, editor, upload = await upload_csv(material_service, team)
    service = Importer(materials)
    await service.initialize()
    await materials.database.uploads.update_one(
        {"org_id": str(editor.org_id), "kg_id": str(upload.kg_id), "id": str(upload.upload_id)},
        {"$set": {"blob.material_id": str(uuid4())}},
    )
    with pytest.raises(DomainError) as foreign:
        await service.plan(
            editor,
            ImportSource(
                kg_id=upload.kg_id,
                upload_id=upload.upload_id,
                format="csv",
                idempotency_key="forged-plan",
            ),
        )
    assert foreign.value.code == ErrorCode.RESOURCE_NOT_ACCESSIBLE
    assert not await materials.database.import_plans.find_one({"kg_id": str(upload.kg_id)})


async def test_import_plan_detects_upload_reference_change_while_reading_original(
    material_service, team, monkeypatch
):
    from domain_kg.storage.blobs import BlobStore

    materials, editor, upload = await upload_csv(material_service, team)
    service = Importer(materials)
    await service.initialize()
    original = BlobStore.open

    async def racing_open(store, reference):
        async for part in original(store, reference):
            yield part
        await materials.database.uploads.update_one(
            {"org_id": str(editor.org_id), "kg_id": str(upload.kg_id), "id": str(upload.upload_id)},
            {"$set": {"version_id": str(uuid4())}},
        )

    monkeypatch.setattr(BlobStore, "open", racing_open)
    with pytest.raises(DomainError) as changed:
        await service.plan(
            editor,
            ImportSource(
                kg_id=upload.kg_id,
                upload_id=upload.upload_id,
                format="csv",
                idempotency_key="racing-plan",
            ),
        )
    assert changed.value.code in (ErrorCode.VERSION_CONFLICT, ErrorCode.RESOURCE_NOT_ACCESSIBLE)
    assert not await materials.database.import_plans.find_one({"kg_id": str(upload.kg_id)})


async def test_explicit_legacy_origin_keeps_ids_across_changed_uploads(material_service, team):
    materials, editor, upload = await upload_csv(material_service, team)
    service = Importer(materials)
    await service.initialize()
    first = await service.plan(
        editor,
        ImportSource(
            kg_id=upload.kg_id,
            upload_id=upload.upload_id,
            format="csv",
            idempotency_key="origin-one",
            origin="legacy-system",
        ),
    )
    _, _, updated = await upload_csv(material_service, team, key="new-source", data=CSV + b"\n")
    second = await service.plan(
        editor,
        ImportSource(
            kg_id=upload.kg_id,
            upload_id=updated.upload_id,
            format="csv",
            idempotency_key="origin-two",
            origin="legacy-system",
        ),
    )
    records = [
        await materials.database.import_plans.find_one({"id": str(plan.id)})
        for plan in (first, second)
    ]
    assert first.content_hash != second.content_hash
    assert {entity["entity_id"] for entity in records[0]["graph"]["entities"]} == {
        entity["entity_id"] for entity in records[1]["graph"]["entities"]
    }


async def test_import_plan_replay_authenticates_its_sealed_normalized_records(
    material_service, team
):
    materials, editor, upload = await upload_csv(material_service, team)
    service = Importer(materials)
    await service.initialize()
    source = ImportSource(
        kg_id=upload.kg_id, upload_id=upload.upload_id, format="csv", idempotency_key="sealed-plan"
    )
    plan = await service.plan(editor, source)
    await materials.database.import_plans.update_one(
        {"org_id": str(editor.org_id), "kg_id": str(upload.kg_id), "id": str(plan.id)},
        {"$set": {"graph.entities.0.name": "Forged name not present in export"}},
    )
    with pytest.raises(DomainError) as altered:
        await service.plan(editor, source)
    assert altered.value.code == ErrorCode.EVIDENCE_VALIDATION_FAILED
