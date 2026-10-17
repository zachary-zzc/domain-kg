"""Restored native and legacy sources retain distinct trust and exact original bytes."""
# ruff: noqa: F811

from uuid import UUID

import pytest
from test_legacy_import import upload_csv
from test_publication import publishing  # noqa: F401
from test_restore_preparation import setup_restore

from domain_kg.identity.models import Action
from domain_kg.imports.models import ImportSource
from domain_kg.imports.service import Importer
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.operations.restore_activation import RestoreActivation
from domain_kg.operations.restore_authority import RestoreAuthority
from domain_kg.operations.restore_control import RestoreControl
from domain_kg.operations.restore_human_history import RestoreHumanHistory
from domain_kg.operations.restore_preparation import RestorePreparation
from domain_kg.operations.restore_projections import RestoreProjections
from domain_kg.operations.restore_stores import RestoreStores
from domain_kg.retrieval.models import SearchFilters, SearchRequest
from domain_kg.retrieval.service import RetrievalService

pytestmark = pytest.mark.storage


async def test_restore_keeps_import_explicitly_unverified_with_scoped_history(
    publishing, material_service, team, catalog
):
    _, editor, queue, coordinator, _, _ = publishing
    imported = []
    registry = coordinator.providers.registry
    profile = next(iter(registry.profiles.values()))
    registry.profiles[profile.id] = profile.model_copy(update={"allow_restricted": True})

    async def add_import(source):
        materials, access, upload = await upload_csv(material_service, team)
        importer = Importer(materials)
        await importer.initialize()
        plan = await importer.plan(
            access,
            ImportSource(
                kg_id=source.kg_id,
                upload_id=upload.upload_id,
                format="csv",
                idempotency_key="restore-import-plan",
            ),
        )
        receipt = await importer._admit(access, plan)
        job = await queue.claim("before-restore-import", kinds=("import",))
        imported.append(UUID((await queue._current(job))["material_id"]))
        await JobRunner(queue, configured_pipeline(queue, coordinator.providers)).run_job(job)
        assert (await queue.get(access, receipt.job_id)).stage == "ready_with_warnings"

    _, _, job, target, _ = await setup_restore(publishing, team, catalog, before_backup=add_import)
    binding = await RestoreStores(queue, coordinator.providers.registry).provision(job)
    try:
        staged = await RestorePreparation(queue, coordinator.providers).prepare(job)
        assert len(staged.sources) == 2
        await RestoreAuthority(queue, coordinator.providers).stage(job, staged)
        await RestoreControl(queue, coordinator.providers).stage(job, staged)
        await RestoreHumanHistory(queue, coordinator.providers).stage(job, staged)
        parts = await RestoreProjections(queue, coordinator.providers).stage(job, staged)
        await RestoreActivation(queue, coordinator.providers).activate(job, staged, parts)
        access = await queue.policy.resolve_access(
            team[2]["owner"], [target.target.kg_id], Action.SEARCH
        )
        query = SearchRequest(
            kg_ids=(target.target.kg_id,),
            query="Alpha",
            mode="lexical",
            filters=SearchFilters(material_ids=(imported[0],)),
        )
        search = RetrievalService(queue, coordinator.providers)
        assert not (await search.search(access, query)).results
        native = await search.search(access, query.model_copy(update={"filters": SearchFilters()}))
        assert native.results and all(
            hit.support_status != "imported_unverified" for hit in native.results
        )
        explicit = await search.search(
            access,
            query.model_copy(
                update={
                    "filters": SearchFilters(
                        material_ids=(imported[0],),
                        support_statuses=("imported_unverified",),
                    )
                }
            ),
        )
        assert explicit.results and all(
            hit.support_status == "imported_unverified"
            and hit.import_origin.kg_id == target.target.kg_id
            for hit in explicit.results
        )
        from domain_kg.citations.service import CitationService

        citation = await CitationService(queue).resolve(
            access,
            target.target.kg_id,
            explicit.results[0].citation_ids[0],
            include_unverified=True,
        )
        assert citation.support_status == "imported_unverified" and not citation.spans
    finally:
        await binding.stores.vectors.client.delete_collection(binding.stores.vectors.collection)
        await binding.stores.graph.driver.execute_query(
            "MATCH (n {org_id:$org,kg_id:$kg}) DETACH DELETE n",
            org=str(target.target.org_id),
            kg=str(target.target.kg_id),
        )
