"""Expired import cleanup removes source-owned maps and decoded export content."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from test_legacy_import import upload_csv
from test_publication import make_publishing

from domain_kg.identity.models import Action
from domain_kg.imports.models import ImportSource
from domain_kg.imports.service import Importer
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.materials.lifecycle import MaterialLifecycle
from domain_kg.materials.models import RemoveMaterial
from domain_kg.materials.purge_models import PurgeMaterial

pytestmark = pytest.mark.storage


async def test_purge_unpublished_import_removes_decoded_graph_and_source_mappings(
    material_service, team
):
    materials, editor, queue, coordinator, _, _ = await make_publishing(
        material_service, team, restricted=True
    )
    _, _, uploaded = await upload_csv(material_service, team)
    importer = Importer(materials, coordinator.providers)
    await importer.initialize()
    plan = await importer.plan(
        editor,
        ImportSource(
            kg_id=uploaded.kg_id,
            upload_id=uploaded.upload_id,
            format="csv",
            idempotency_key="purge-plan",
        ),
    )
    receipt = await importer.execute(editor, plan)
    job = await queue.claim("prepare-purge-import", kinds=("import",))
    pipeline = configured_pipeline(queue, coordinator.providers)
    for step in pipeline.import_steps[:5]:
        checkpoint = await step.execute(job)
        await queue.checkpoint(job, checkpoint)
    raw = await queue._current(job)
    material = UUID(raw["material_id"])
    await queue.cancel(editor, receipt.job_id, idempotency_key="cancel-import-before-purge")
    lifecycle = MaterialLifecycle(materials, queue)
    removed = await lifecycle.remove(
        editor,
        RemoveMaterial(
            kg_id=uploaded.kg_id,
            material_id=material,
            expected_revision=0,
            action="delete",
            idempotency_key="remove-import-before-purge",
        ),
    )
    ns = {"org_id": str(editor.org_id), "kg_id": str(uploaded.kg_id)}
    await queue.database.material_access.update_one(
        ns | {"id": str(material)},
        {"$set": {"purge_after": datetime.now(UTC) - timedelta(seconds=1)}},
    )
    owner = await queue.policy.resolve_access(team[2]["owner"], [uploaded.kg_id], Action.PURGE)
    pending = await lifecycle.purge(
        owner,
        PurgeMaterial(
            kg_id=uploaded.kg_id,
            material_id=material,
            expected_revision=removed.revision,
            reason="Expired synthetic import retention",
            idempotency_key="purge-import",
        ),
    )
    purge = await queue.claim("purge-import", kinds=("purge_material",))
    assert purge and purge.id == pending.job_id
    await JobRunner(queue, pipeline).run_job(purge)
    assert (await queue.get(owner, purge.id)).stage == "ready"
    stores = await queue.router.for_sources(owner, uploaded.kg_id)
    for collection in ("import_id_mappings", "import_assertion_mappings"):
        assert (
            await stores.authority._database[collection].count_documents(
                ns | {"material_id": str(material)}
            )
            == 0
        )
    saved = await queue.database.import_plans.find_one(ns | {"id": str(plan.id)})
    assert saved["state"] == "purged" and "graph" not in saved and saved["graph_hash"]
