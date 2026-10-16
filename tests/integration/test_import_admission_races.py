"""Admission replay survives another caller winning while original validation is in flight."""

import pytest
from test_legacy_import import upload_csv

from domain_kg.imports.models import ImportSource
from domain_kg.imports.service import Importer
from domain_kg.storage.blobs import BlobStore

pytestmark = pytest.mark.storage


async def test_import_admission_race_during_original_read_returns_the_winning_job(
    material_service, team, monkeypatch
):
    materials, editor, upload = await upload_csv(material_service, team)
    importer = Importer(materials)
    await importer.initialize()
    plan = await importer.plan(
        editor,
        ImportSource(
            kg_id=upload.kg_id,
            upload_id=upload.upload_id,
            format="csv",
            idempotency_key="read-race-plan",
        ),
    )
    original_open = BlobStore.open
    winner = None
    entered = False

    async def racing_open(store, reference):
        nonlocal winner, entered
        async for part in original_open(store, reference):
            yield part
        if not entered:
            entered = True
            winner = await importer._admit(editor, plan)

    monkeypatch.setattr(BlobStore, "open", racing_open)
    receipt = await importer._admit(editor, plan)
    assert receipt == winner
    assert await materials.database.jobs.count_documents({"kg_id": str(plan.kg_id)}) == 1
    assert (
        await materials.database.import_source_origins.count_documents({"kg_id": str(plan.kg_id)})
        == 1
    )
