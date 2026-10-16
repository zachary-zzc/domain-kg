"""Imported stable identities retain explicit human overrides and independent ownership."""

import pytest
from test_import_recovery import imported
from test_legacy_import import upload_csv

from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.imports.models import ImportSource
from domain_kg.imports.service import Importer
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.materials.lifecycle import MaterialLifecycle
from domain_kg.materials.models import RemoveMaterial
from domain_kg.retrieval.models import SearchFilters, SearchRequest, SubgraphRequest
from domain_kg.retrieval.service import RetrievalService
from domain_kg.revisions.models import NodeChanges, NodeUpdate
from domain_kg.revisions.publication import accepted_document
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


async def test_owner_curation_survives_same_origin_import_and_independent_source_removal(
    material_service, team
):
    fixture, view = await imported(material_service, team)
    materials, editor, queue, coordinator, _, _ = fixture
    source = view.sources[0]
    document, _, _ = await accepted_document(queue, editor, view.projections[0])
    entity = next(
        row.observation.entity.id
        for row in document.rows
        if row.observation and row.observation.name == "Alpha"
    )
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.CURATE)
    revision = await RevisionService(queue, coordinator.providers).update_node(
        owner,
        NodeUpdate(
            kg_id=source.kg_id,
            entity_id=entity,
            expected_revision=0,
            curation_scope="kg_public",
            changes=NodeChanges(name="Reviewed Alpha"),
            reason="An explicit owner-authored label",
            idempotency_key="review-import-label",
        ),
    )
    curate = await queue.claim("import-curator", kinds=("curate",))
    assert curate.id == revision.job_id
    runner = JobRunner(queue, configured_pipeline(queue, coordinator.providers))
    await runner.run_job(curate)
    status = await queue.get(owner, curate.id)
    assert status.stage == "ready", status.warnings
    _, _, upload = await upload_csv(material_service, team, key="independent-import-upload")
    importer = Importer(materials, coordinator.providers)
    plan = await importer.plan(
        editor,
        ImportSource(
            kg_id=source.kg_id,
            upload_id=upload.upload_id,
            format="csv",
            origin="stable-legacy-fixture",
            idempotency_key="independent-import-plan",
        ),
    )
    receipt = await importer.execute(editor, plan)
    job = await queue.claim("second-import", kinds=("import",))
    assert job.id == receipt.job_id
    await runner.run_job(job)
    assert (await queue.get(editor, job.id)).stage == "ready_with_warnings"
    filters = SearchFilters.for_curation()
    graph = GraphService(queue)
    node = await graph.get_node(owner, source.kg_id, entity, filters)
    assert node.name == "Reviewed Alpha" and node.observation_count == 2
    row = await queue.database.material_access.find_one(
        {
            "org_id": str(source.org_id),
            "kg_id": str(source.kg_id),
            "id": str(source.material_id),
        }
    )
    await MaterialLifecycle(materials, queue).remove(
        editor,
        RemoveMaterial(
            kg_id=source.kg_id,
            material_id=source.material_id,
            action="delete",
            expected_revision=row["revision"],
            idempotency_key="remove-first-import",
        ),
    )
    node = await graph.get_node(owner, source.kg_id, entity, filters)
    assert node.name == "Reviewed Alpha" and node.observation_count == 1
    subgraph = await graph.subgraph(
        owner,
        SubgraphRequest(
            kg_id=source.kg_id,
            seed_ids=(entity,),
            filters=filters,
        ),
    )
    assert subgraph.edges and all(
        edge.source.material_id != source.material_id for edge in subgraph.edges if edge.source
    )
    response = await RetrievalService(queue, coordinator.providers).search(
        owner,
        SearchRequest(
            kg_ids=(source.kg_id,),
            query="Alpha",
            mode="lexical",
            filters=filters,
        ),
    )
    assert response.results and all(
        hit.material_id != source.material_id for hit in response.results
    )
