"""Every import format preserves trust and ownership through ordinary lifecycle work."""

import json
from uuid import UUID

import pytest
from qdrant_client import models
from test_entity_resolution import TEXT
from test_legacy_import import CSV, upload_csv
from test_publication import make_publishing

from domain_kg.graph.service import GraphService
from domain_kg.imports.models import ImportSource
from domain_kg.imports.service import Importer
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.materials.lifecycle import MaterialLifecycle
from domain_kg.materials.models import RemoveMaterial, ReplaceMaterial, RestoreMaterial
from domain_kg.retrieval.models import SearchFilters, SearchRequest
from domain_kg.retrieval.service import RetrievalService
from domain_kg.revisions.journal import PublicationJournal
from domain_kg.revisions.repair import RepairService

pytestmark = pytest.mark.storage

GRAPHML = b"""<graphml xmlns="http://graphml.graphdrawing.org/xmlns">
<key id="name" for="node" attr.name="entity_name" attr.type="string"/>
<key id="source" for="edge" attr.name="source_id" attr.type="string"/>
<key id="predicate" for="edge" attr.name="relation" attr.type="string"/>
<graph edgedefault="directed"><node id="a"><data key="name">Alpha</data></node>
<node id="b"><data key="name">Beta</data></node>
<edge source="a" target="b"><data key="source">a.pdf</data>
<data key="predicate">uses</data></edge></graph></graphml>"""
SNAPSHOT = json.dumps(
    {
        "format_version": 1,
        "engine_version": "1.5.7",
        "consistent": True,
        "documents": [{"id": "a.pdf", "sha256": "a" * 64}],
        "nodes": [
            {"id": "a", "name": "Alpha", "entity_type": "concept"},
            {"id": "b", "name": "Beta", "entity_type": "concept"},
        ],
        "edges": [
            {"id": "e", "source": "a", "target": "b", "predicate": "uses", "source_ids": ["a.pdf"]}
        ],
    }
).encode()


async def imported(material_service, team, format="csv", body=CSV):
    fixture = await make_publishing(material_service, team, restricted=True)
    materials, editor, queue, coordinator, _, _ = fixture
    _, _, uploaded = await upload_csv(material_service, team, data=body)
    service = Importer(materials, coordinator.providers)
    await service.initialize()
    plan = await service.plan(
        editor,
        ImportSource(
            kg_id=uploaded.kg_id,
            upload_id=uploaded.upload_id,
            format=format,
            origin="stable-legacy-fixture",
            idempotency_key="plan",
        ),
    )
    receipt = await service.execute(editor, plan)
    job = await queue.claim("import", kinds=("import",))
    await JobRunner(queue, configured_pipeline(queue, coordinator.providers)).run_job(job)
    assert (await queue.get(editor, receipt.job_id)).stage == "ready_with_warnings"
    return fixture, (await PublicationJournal(queue).load(editor, uploaded.kg_id))


@pytest.mark.parametrize(
    "format,body",
    [("csv", CSV), ("graphml", GRAPHML), ("lightrag", SNAPSHOT)],
    ids=("csv", "graphml", "lightrag"),
)
async def test_all_formats_keep_mapping_and_origin_through_provider_free_repair_restore(
    material_service, team, monkeypatch, format, body
):
    fixture, view = await imported(material_service, team, format, body)
    materials, editor, queue, coordinator, _, _ = fixture
    source, origin = view.sources[0], view.projections[0].import_origin
    stores = await queue.router.for_sources(editor, source.kg_id)
    mappings = await stores.authority._database.import_id_mappings.find(
        {
            "org_id": str(source.org_id),
            "kg_id": str(source.kg_id),
            "material_id": str(source.material_id),
            "version_id": str(source.version_id),
        }
    ).to_list()
    assert len(mappings) == 2 and all(row["origin"] == origin.origin for row in mappings)
    filters = SearchFilters(support_statuses=("imported_unverified",))
    node = await GraphService(queue).get_node(
        editor, source.kg_id, UUID(mappings[0]["entity"]["id"]), filters
    )
    assert node.import_origins == (origin,) and not node.descriptions

    async def forbidden(*args, **kwargs):
        pytest.fail("Retained import repair/restore must not invoke model providers")

    monkeypatch.setattr(coordinator.providers, "extract", forbidden)
    monkeypatch.setattr(coordinator.providers, "embeddings", forbidden)
    repair = await RepairService(queue).request(
        editor, source.kg_id, source.material_id, idempotency_key="repair-import"
    )
    job = await queue.claim("repair-import", kinds=("repair",))
    await JobRunner(queue, configured_pipeline(queue, coordinator.providers)).run_job(job)
    assert (await queue.get(editor, repair.job_id)).stage == "ready_with_warnings"
    repaired = await PublicationJournal(queue).load(editor, source.kg_id)
    assert repaired.projections[0].import_origin == origin
    vectors = await queue.router.for_vectors(editor, source.kg_id)
    points, _ = await vectors.client.scroll(
        vectors.collection,
        scroll_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="attempt_id",
                    match=models.MatchValue(value=str(repaired.projections[0].attempt_id)),
                )
            ]
        ),
        limit=1,
    )
    assert points
    await vectors.client.delete(
        vectors.collection, points_selector=models.PointIdsList(points=[points[0].id]), wait=True
    )
    material = await queue.database.material_access.find_one(
        {"org_id": str(source.org_id), "kg_id": str(source.kg_id), "id": str(source.material_id)}
    )
    lifecycle = MaterialLifecycle(materials, queue)
    removed = await lifecycle.remove(
        editor,
        RemoveMaterial(
            kg_id=source.kg_id,
            material_id=source.material_id,
            expected_revision=material["revision"],
            action="delete",
            idempotency_key="delete-import",
        ),
    )
    pending = await lifecycle.restore(
        editor,
        RestoreMaterial(
            kg_id=source.kg_id,
            material_id=source.material_id,
            expected_revision=removed.revision,
            idempotency_key="restore-import",
        ),
    )
    assert pending.state == "pending"
    restore = await queue.claim("restore-import", kinds=("restore_material",))
    await JobRunner(queue, configured_pipeline(queue, coordinator.providers)).run_job(restore)
    assert (await queue.get(editor, pending.job_id)).stage == "ready_with_warnings"
    restored = await PublicationJournal(queue).load(editor, source.kg_id)
    assert restored.projections[0].import_origin == origin
    assert await queue.policy.material_visible(editor, source.kg_id, source.material_id)
    search = RetrievalService(queue, coordinator.providers)
    assert not (
        await search.search(
            editor,
            SearchRequest(
                kg_ids=(source.kg_id,),
                query="Alpha",
                mode="lexical",
            ),
        )
    ).results
    visible = await search.search(
        editor,
        SearchRequest(
            kg_ids=(source.kg_id,),
            query="Alpha",
            mode="lexical",
            filters=filters,
        ),
    )
    assert visible.results and all(hit.import_origin == origin for hit in visible.results)


async def test_native_replacement_does_not_reuse_unverified_import_as_extraction(
    material_service, team
):
    fixture, view = await imported(material_service, team)
    materials, editor, queue, coordinator, _, _ = fixture
    source = view.sources[0]
    receipt = await materials.replace(
        editor,
        ReplaceMaterial(
            kg_id=source.kg_id,
            material_id=source.material_id,
            expected_active_version=source.version_id,
            inline_text=TEXT,
            title="Actual replacement source",
            idempotency_key="replace-import",
        ),
    )
    job = await queue.claim("native-replacement", kinds=("replace",))
    runner = JobRunner(queue, configured_pipeline(queue, coordinator.providers))
    for step in runner.pipeline.steps[:4]:
        checkpoint = await step.execute(job)
        await queue.checkpoint(job, checkpoint)
    await runner.run_job(job)
    status = await queue.get(editor, receipt.job_id)
    assert status.stage == "ready", status.warnings
    replaced = await PublicationJournal(queue).load(editor, source.kg_id)
    assert replaced.sources[0].version_id != source.version_id
    assert replaced.projections[0].import_origin is None
