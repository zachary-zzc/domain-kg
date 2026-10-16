"""Old relation IDs map to independent version-owned support, not only merged facts."""

import pytest
from test_legacy_import import CSV, upload_csv
from test_publication import make_publishing

from domain_kg.imports.models import ImportSource
from domain_kg.imports.service import Importer
from domain_kg.ingestion.pipeline import configured_pipeline

pytestmark = pytest.mark.storage


async def test_import_persists_independent_legacy_relation_and_support_mappings(
    material_service, team
):
    materials, editor, queue, coordinator, _, _ = await make_publishing(
        material_service, team, restricted=True
    )
    data = CSV + b"Alpha,concept,uses,Beta,concept,b.pdf,source B\n"
    _, _, uploaded = await upload_csv(material_service, team, data=data)
    importer = Importer(materials, coordinator.providers)
    await importer.initialize()
    plan = await importer.plan(
        editor,
        ImportSource(
            kg_id=uploaded.kg_id,
            upload_id=uploaded.upload_id,
            format="csv",
            origin="mapping-fixture",
            idempotency_key="mapping-plan",
        ),
    )
    await importer.execute(editor, plan)
    job = await queue.claim("mapping-preparation", kinds=("import",))
    for step in configured_pipeline(queue, coordinator.providers).import_steps[:5]:
        checkpoint = await step.execute(job)
        await queue.checkpoint(job, checkpoint)
    stores = await queue.router.for_sources(editor, uploaded.kg_id)
    rows = await stores.authority._database.import_assertion_mappings.find(
        {
            "org_id": str(editor.org_id),
            "kg_id": str(uploaded.kg_id),
            "version_id": str(job.target_id),
            "plan_id": str(plan.id),
        }
    ).to_list()
    assert len(rows) == 2
    assert len({row["assertion"]["id"] for row in rows}) == 1
    assert len({row["support"]["id"] for row in rows}) == 2
    assert {row["legacy_source_id"] for row in rows} == {"a.pdf", "b.pdf"}
    assert all(row["legacy_id"] and row["origin"] == "mapping-fixture" for row in rows)
