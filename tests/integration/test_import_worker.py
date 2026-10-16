"""Imported claims publish only through real fenced graph/vector readiness checks."""

import pytest
from test_legacy_import import upload_csv
from test_publication import make_publishing

from domain_kg.imports.models import ImportSource
from domain_kg.imports.service import Importer
from domain_kg.imports.worker import import_steps  # noqa: F401
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.retrieval.models import SearchRequest
from domain_kg.retrieval.service import RetrievalService
from domain_kg.revisions.journal import PublicationJournal

pytestmark = pytest.mark.storage


async def test_import_worker_uses_embeddings_without_extraction_and_default_search_excludes_it(
    material_service, team
):
    def no_extraction(*args):
        raise AssertionError("Legacy claims must not be relabeled as provider extraction.")

    fixture = await make_publishing(
        material_service, team, restricted=True, extraction_factory=no_extraction
    )
    _, editor, queue, coordinator, _, faults = fixture
    materials, editor, uploaded = await upload_csv(material_service, team)
    importer = Importer(materials)
    await importer.initialize()
    plan = await importer.plan(
        editor,
        ImportSource(
            kg_id=uploaded.kg_id,
            upload_id=uploaded.upload_id,
            format="csv",
            idempotency_key="worker-plan",
        ),
    )
    receipt = await importer._admit(editor, plan)
    assert (await PublicationJournal(queue).load_metadata(editor, uploaded.kg_id)).sources == ()
    job = await queue.claim("import-worker", kinds=("import",))
    assert job and job.id == receipt.job_id
    await JobRunner(queue, configured_pipeline(queue, coordinator.providers)).run_job(job)
    status = await queue.get(editor, receipt.job_id)
    assert status.stage == "ready_with_warnings", status.warnings
    assert "LEGACY_CLAIMS_UNVERIFIED" in status.warnings
    view = await PublicationJournal(queue).load(editor, uploaded.kg_id)
    assert len(view.sources) == 1 and view.projections[0].import_origin.plan_id == plan.id
    assert faults["embedding_inputs"]
    for mode in ("lexical", "vector", "graph", "fast", "deep"):
        response = await RetrievalService(queue, coordinator.providers).search(
            editor, SearchRequest(kg_ids=(uploaded.kg_id,), query="Alpha", mode=mode)
        )
        assert not response.results
    from domain_kg.citations.service import CitationService
    from domain_kg.errors import DomainError
    from domain_kg.graph.service import GraphService
    from domain_kg.identity.models import Action
    from domain_kg.materials.inspection import MaterialInspection
    from domain_kg.retrieval.models import SearchFilters, SubgraphRequest

    filters = SearchFilters(support_statuses=("imported_unverified",))
    for mode in ("lexical", "vector", "graph", "fast", "deep"):
        response = await RetrievalService(queue, coordinator.providers).search(
            editor,
            SearchRequest(kg_ids=(uploaded.kg_id,), query="Alpha", mode=mode, filters=filters),
        )
        assert response.results, mode
        assert all(
            hit.support_status == "imported_unverified" and hit.import_origin.plan_id == plan.id
            for hit in response.results
        )
        assert all(
            step.support_status == "imported_unverified" and step.import_origin.plan_id == plan.id
            for hit in response.results
            for path in hit.graph_paths
            for step in path
        )
    hit = response.results[0]
    with pytest.raises(DomainError):
        await CitationService(queue).resolve(editor, uploaded.kg_id, hit.citation_ids[0])
    citation = await CitationService(queue).resolve(
        editor, uploaded.kg_id, hit.citation_ids[0], include_unverified=True
    )
    assert citation.support_status == "imported_unverified" and not citation.spans
    assert (
        citation.import_origin.plan_id == plan.id
        and "include_unverified=true" in citation.resolver_url
    )
    graph = GraphService(queue)
    node = await graph.get_node(editor, uploaded.kg_id, hit.entity_ids[0], filters)
    assert node.import_origins == (view.projections[0].import_origin,)
    subgraph = await graph.subgraph(
        editor, SubgraphRequest(kg_id=uploaded.kg_id, seed_ids=(node.id,), filters=filters)
    )
    assert subgraph.edges and all(
        edge.status == "imported_unverified" and not edge.spans for edge in subgraph.edges
    )
    detail = await MaterialInspection(queue).get(
        editor, uploaded.kg_id, hit.material_id, include_unverified=True
    )
    assert detail.import_origin == hit.import_origin and detail.chunks
    reader = await queue.policy.resolve_access(team[2]["reader"], [uploaded.kg_id], Action.READ)
    denied = await RetrievalService(queue, coordinator.providers).search(
        reader, SearchRequest(kg_ids=(uploaded.kg_id,), query="Alpha", mode="fast", filters=filters)
    )
    assert not denied.results
    with pytest.raises(DomainError):
        await CitationService(queue).resolve(
            reader, uploaded.kg_id, hit.citation_ids[0], include_unverified=True
        )
    from domain_kg.revisions.models import CurationCommand
    from domain_kg.revisions.service import RevisionService

    await RevisionService(queue, coordinator.providers).curate_node(
        editor,
        CurationCommand(
            kg_id=uploaded.kg_id,
            entity_id=node.id,
            action="suppress",
            expected_revision=0,
            curation_scope="source",
            source_ids=(hit.material_id,),
            reason="Suppress all raw export contexts for this imported label",
            idempotency_key="suppress-import",
        ),
    )
    for mode in ("lexical", "vector", "graph", "fast", "deep"):
        hidden = await RetrievalService(queue, coordinator.providers).search(
            editor,
            SearchRequest(kg_ids=(uploaded.kg_id,), query="Alpha", mode=mode, filters=filters),
        )
        assert not hidden.results
    with pytest.raises(DomainError):
        await CitationService(queue).resolve(
            editor, uploaded.kg_id, hit.citation_ids[0], include_unverified=True
        )
    assert not (
        await MaterialInspection(queue).get(
            editor, uploaded.kg_id, hit.material_id, include_unverified=True
        )
    ).chunks


async def test_import_projection_failure_keeps_old_view_and_retry_uses_new_attempt(
    material_service, team, monkeypatch
):
    from domain_kg.errors import DomainError, ErrorCode
    from domain_kg.storage.qdrant import VectorProjection

    fixture = await make_publishing(material_service, team, restricted=True)
    _, editor, queue, coordinator, _, _ = fixture
    materials, editor, uploaded = await upload_csv(material_service, team)
    importer = Importer(materials)
    await importer.initialize()
    plan = await importer.plan(
        editor,
        ImportSource(
            kg_id=uploaded.kg_id,
            upload_id=uploaded.upload_id,
            format="csv",
            idempotency_key="failed-import-plan",
        ),
    )
    receipt = await importer._admit(editor, plan)
    original = VectorProjection.write

    async def fail(*args, **kwargs):
        raise DomainError(ErrorCode.BACKEND_UNAVAILABLE, "Fixture vector failure.")

    monkeypatch.setattr(VectorProjection, "write", fail)
    job = await queue.claim("failing-import", kinds=("import",))
    runner = JobRunner(queue, configured_pipeline(queue, coordinator.providers))
    await runner.run_job(job)
    assert (await queue.get(editor, receipt.job_id)).stage == "failed"
    assert (await PublicationJournal(queue).load_metadata(editor, uploaded.kg_id)).sources == ()
    monkeypatch.setattr(VectorProjection, "write", original)
    await queue.retry(editor, receipt.job_id, idempotency_key="retry-import")
    retried = await queue.claim("retry-import", kinds=("import",))
    assert retried.attempt_id != job.attempt_id
    await runner.run_job(retried)
    assert (await queue.get(editor, receipt.job_id)).stage == "ready_with_warnings"
    view = await PublicationJournal(queue).load(editor, uploaded.kg_id)
    assert view.projections[0].attempt_id == retried.attempt_id
    assert view.projections[0].import_origin.plan_id == plan.id


async def test_import_worker_rechecks_revocation_before_resolving_and_publishing(
    material_service, team
):
    from domain_kg.identity.models import Action

    fixture = await make_publishing(material_service, team, restricted=True)
    _, editor, queue, coordinator, _, _ = fixture
    materials, editor, uploaded = await upload_csv(material_service, team)
    importer = Importer(materials)
    await importer.initialize()
    plan = await importer.plan(
        editor,
        ImportSource(
            kg_id=uploaded.kg_id,
            upload_id=uploaded.upload_id,
            format="csv",
            idempotency_key="revoked-worker-plan",
        ),
    )
    receipt = await importer._admit(editor, plan)
    job = await queue.claim("revoked-import", kinds=("import",))
    pipeline = configured_pipeline(queue, coordinator.providers)
    for step in pipeline.import_steps[:4]:
        checkpoint = await step.execute(job)
        await queue.checkpoint(job, checkpoint)
    owner = await team[1].resolve_access(team[2]["owner"], [uploaded.kg_id], Action.MANAGE_GRANTS)
    await team[1].set_grant(
        owner, uploaded.kg_id, "member", team[3]["editor"].id, None, idempotency_key="revoke-worker"
    )
    await JobRunner(queue, pipeline).run_job(job)
    owner = await team[1].resolve_access(team[2]["owner"], [uploaded.kg_id], Action.READ)
    assert (await PublicationJournal(queue).load_metadata(owner, uploaded.kg_id)).sources == ()
    raw = await queue.database.jobs.find_one({"id": str(receipt.job_id)})
    assert raw["stage"] == "failed"
