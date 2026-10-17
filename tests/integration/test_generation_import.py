"""Mixed native, manual and imported evidence keeps its original trust after rebuild."""

import pytest
from test_legacy_import import upload_csv
from test_maintenance_admission import accepted_human_edit
from test_publication import make_publishing

from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.imports.models import ImportSource
from domain_kg.imports.service import Importer
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.operations.generation import GenerationService
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import GenerationCommand
from domain_kg.retrieval.models import SearchFilters, SearchRequest
from domain_kg.retrieval.service import RetrievalService
from domain_kg.revisions.journal import PublicationJournal
from domain_kg.revisions.publication import verify_accepted

pytestmark = pytest.mark.storage


async def test_generation_keeps_native_manual_and_imported_trust(
    material_service, team, monkeypatch
):
    fixture = await make_publishing(material_service, team, restricted=True)
    source, alpha, _ = await accepted_human_edit(fixture)
    _, editor, queue, coordinator, _, _ = fixture
    materials, uploader, uploaded = await upload_csv(material_service, team)
    importer = Importer(materials)
    await importer.initialize()
    plan = await importer.plan(
        uploader,
        ImportSource(
            kg_id=source.kg_id,
            upload_id=uploaded.upload_id,
            format="csv",
            idempotency_key="generation-import-plan",
        ),
    )
    await importer._admit(uploader, plan)
    imported = await queue.claim("generation-import", kinds=("import",))
    await JobRunner(queue, configured_pipeline(queue, coordinator.providers)).run_job(imported)
    assert (await queue.get(editor, imported.id)).stage == "ready_with_warnings"
    before = await PublicationJournal(queue).load_metadata(editor, source.kg_id)
    legacy = next(binding for binding in before.projections if binding.import_origin)

    async def forbidden(*args, **kwargs):
        pytest.fail("rebuild and lexical lookup must not call a provider")

    monkeypatch.setattr(coordinator.providers, "embeddings", forbidden)
    monkeypatch.setattr(coordinator.providers, "extract", forbidden)
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.CONFIGURE)
    await OwnerOperations(queue).submit(
        owner,
        GenerationCommand(
            kg_id=source.kg_id, expected_published_revision=3, idempotency_key="mixed-generation"
        ),
    )
    rebuild = await queue.claim("mixed-generation", kinds=("generation",))
    await GenerationService(queue).run(rebuild)
    after = await PublicationJournal(queue).load_metadata(owner, source.kg_id)
    assert len(after.projections) == 3 and len(after.sources) == 2 and len(after.curations) == 1
    for binding in after.projections:
        await verify_accepted(queue, owner, binding)
    assert (
        next(binding for binding in after.projections if binding.import_origin).import_origin
        == legacy.import_origin
    )
    assert (await GraphService(queue).get_node(owner, source.kg_id, alpha)).name == "Reviewed Alpha"
    search = RetrievalService(queue, coordinator.providers)
    default = await search.search(
        owner, SearchRequest(kg_ids=(source.kg_id,), query="Alpha", mode="lexical")
    )
    assert default.results and all(hit.import_origin is None for hit in default.results)
    explicit = await search.search(
        owner,
        SearchRequest(
            kg_ids=(source.kg_id,),
            query="Alpha",
            mode="lexical",
            filters=SearchFilters(
                material_ids=(legacy.source.material_id,), support_statuses=("imported_unverified",)
            ),
        ),
    )
    assert explicit.results and all(
        hit.support_status == "imported_unverified" and hit.import_origin == legacy.import_origin
        for hit in explicit.results
    )
