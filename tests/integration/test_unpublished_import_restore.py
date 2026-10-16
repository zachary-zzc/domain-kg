"""An interrupted import resumes as unverified import work under a new restore actor."""

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
from domain_kg.materials.models import RemoveMaterial, RestoreMaterial
from domain_kg.retrieval.models import SearchFilters, SearchRequest
from domain_kg.retrieval.service import RetrievalService
from domain_kg.revisions.journal import PublicationJournal

pytestmark = pytest.mark.storage


async def test_owner_restores_unpublished_import_without_extraction_or_trust_promotion(
    material_service, team
):
    def no_extraction(*args):
        pytest.fail("Import restoration must not invoke document extraction")

    materials, editor, queue, coordinator, _, _ = await make_publishing(
        material_service, team, restricted=True, extraction_factory=no_extraction
    )
    _, _, uploaded = await upload_csv(material_service, team)
    service = Importer(materials, coordinator.providers)
    await service.initialize()
    plan = await service.plan(
        editor,
        ImportSource(
            kg_id=uploaded.kg_id,
            upload_id=uploaded.upload_id,
            format="csv",
            idempotency_key="unpublished-import",
        ),
    )
    admitted = await service.execute(editor, plan)
    raw = await queue.database.jobs.find_one({"id": str(admitted.job_id)})
    material = UUID(raw["material_id"])
    lifecycle = MaterialLifecycle(materials, queue)
    removed = await lifecycle.remove(
        editor,
        RemoveMaterial(
            kg_id=uploaded.kg_id,
            material_id=material,
            action="delete",
            expected_revision=0,
            idempotency_key="remove-unpublished-import",
        ),
    )
    owner = await queue.policy.resolve_access(team[2]["owner"], [uploaded.kg_id], Action.REPLACE)
    command = RestoreMaterial(
        kg_id=uploaded.kg_id,
        material_id=material,
        expected_revision=removed.revision,
        idempotency_key="owner-restore-unpublished-import",
    )
    pending = await lifecycle.restore(owner, command)
    assert pending.state == "pending" and pending.job_id
    assert not await queue.policy.material_visible(owner, uploaded.kg_id, material)
    job = await queue.claim("restore-import-owner", kinds=("reprocess_material",))
    assert job and job.id == pending.job_id
    await JobRunner(queue, configured_pipeline(queue, coordinator.providers)).run_job(job)
    status = await queue.get(owner, job.id)
    assert status.stage == "ready_with_warnings", status.warnings
    assert (await lifecycle.restore(owner, command)).state == "restored"
    view = await PublicationJournal(queue).load(owner, uploaded.kg_id)
    assert view.projections[0].import_origin.plan_id == plan.id
    search = RetrievalService(queue, coordinator.providers)
    assert not (
        await search.search(
            owner,
            SearchRequest(
                kg_ids=(uploaded.kg_id,),
                query="Alpha",
                mode="lexical",
            ),
        )
    ).results
    response = await search.search(
        owner,
        SearchRequest(
            kg_ids=(uploaded.kg_id,),
            query="Alpha",
            mode="lexical",
            filters=SearchFilters(support_statuses=("imported_unverified",)),
        ),
    )
    assert response.results and all(
        hit.support_status == "imported_unverified" for hit in response.results
    )
