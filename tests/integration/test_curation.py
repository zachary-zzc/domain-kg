"""Durable human overrides are independent of source observations and grants."""
# ruff: noqa: F811

import pytest
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.graph.service import GraphService
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.retrieval.models import SearchFilters, SearchRequest
from domain_kg.retrieval.service import RetrievalService
from domain_kg.revisions.models import NodeChanges, NodeUpdate
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


async def test_source_scoped_rename_is_durable_and_searchable_without_changing_originals(
    publishing,
):
    _, editor, queue, coordinator, _, _ = publishing
    sources = []
    for key in ("curation-a", "curation-b"):
        receipt, job, checkpoint = await prepare(publishing, key=key)
        await coordinator.publish(editor, await ready(publishing, job, checkpoint))
        sources.append(receipt)
    graph = GraphService(queue)
    request = SearchRequest(kg_ids=editor.kg_ids, query="Alpha", mode="graph")
    scope = await graph.resolver.resolve(editor, request, job.kg_id)
    data = await graph.repository.open(scope)
    alpha = next(item.entity.id for item in data.observations if item.name == "Alpha")
    revisions = RevisionService(queue, coordinator.providers)
    command = NodeUpdate(
        kg_id=job.kg_id,
        entity_id=alpha,
        expected_revision=0,
        curation_scope="source",
        source_ids=(sources[0].material_id,),
        changes=NodeChanges(name="Corrected Alpha", description="Human correction"),
        reason="Reviewed the source terminology.",
        idempotency_key="curation-rename",
    )
    receipt = await revisions.update_node(editor, command)
    assert receipt.state == "pending" and receipt.job_id
    assert await revisions.update_node(editor, command) == receipt
    assert (await graph.get_node(editor, job.kg_id, alpha)).name == "Alpha"
    leased = await queue.claim("curation-worker")
    assert leased.id == receipt.job_id
    await JobRunner(queue, configured_pipeline(queue, coordinator.providers)).run_job(leased)
    assert (await queue.get(editor, receipt.job_id)).stage == "ready"
    changed = await graph.get_node(editor, job.kg_id, alpha)
    assert changed.name == "Corrected Alpha"
    assert {item.name for item in changed.observations} == {"Alpha"}
    assert changed.curations and changed.curations[0].provenance.reason == command.reason
    source_b = await graph.get_node(
        editor, job.kg_id, alpha, SearchFilters(material_ids=(sources[1].material_id,))
    )
    assert source_b.name == "Alpha" and not source_b.curations
    search = RetrievalService(queue, coordinator.providers)
    for mode in ("lexical", "vector", "deep"):
        response = await search.search(
            editor, SearchRequest(kg_ids=editor.kg_ids, query="Corrected Alpha", mode=mode)
        )
        manual = [hit for hit in response.results if hit.kind == "manual"]
        assert manual and manual[0].curation_revision_id == receipt.revision_id
        assert manual[0].version_id is None and manual[0].chunk_id is None
        assert manual[0].provenance.reason == command.reason
    for mode in ("lexical", "vector", "graph", "deep"):
        response = await search.search(
            editor,
            SearchRequest(
                kg_ids=editor.kg_ids,
                query="Alpha",
                mode=mode,
                filters=SearchFilters(material_ids=(sources[1].material_id,)),
            ),
        )
        assert all(hit.kind == "source" and "Corrected" not in hit.text for hit in response.results)


async def publish_curation(queue, coordinator, editor, receipt):
    leased = await queue.claim("curation-fixture-worker")
    assert leased.id == receipt.job_id
    await JobRunner(queue, configured_pipeline(queue, coordinator.providers)).run_job(leased)
    status = await queue.get(editor, receipt.job_id)
    assert status.stage == "ready", status.warnings


async def alpha_id(queue, access, kg):
    graph = GraphService(queue)
    scope = await graph.resolver.resolve(
        access, SearchRequest(kg_ids=access.kg_ids, query="Alpha", mode="graph"), kg
    )
    return next(
        item.entity.id
        for item in (await graph.repository.open(scope)).observations
        if item.name == "Alpha"
    )


async def test_manual_fields_survive_replacement_and_metadata_edits_reuse_vectors(
    publishing, monkeypatch
):
    from test_entity_resolution import TEXT

    from domain_kg.materials.models import ReplaceMaterial

    _, editor, queue, coordinator, _, faults = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    alpha = await alpha_id(queue, editor, job.kg_id)
    revisions = RevisionService(queue, coordinator.providers)
    command = NodeUpdate(
        kg_id=job.kg_id,
        entity_id=alpha,
        expected_revision=0,
        source_ids=(source.material_id,),
        changes=NodeChanges(name="Reviewed Alpha", description="An explicit human field."),
        reason="Domain review",
        idempotency_key="durable-human-fields",
    )
    receipt = await revisions.update_node(editor, command)
    await publish_curation(queue, coordinator, editor, receipt)

    async def forbidden(*args, **kwargs):
        pytest.fail("unchanged source text and nonembedded node tags require no model work")

    with monkeypatch.context() as patch:
        patch.setattr(coordinator.providers, "extract", forbidden)
        patch.setattr(coordinator.providers, "embeddings", forbidden)
        successor, worker, checkpoint = await prepare(
            publishing,
            command=ReplaceMaterial(
                kg_id=job.kg_id,
                material_id=source.material_id,
                expected_active_version=source.version_id,
                title="Same source, new version",
                inline_text=TEXT,
                idempotency_key="curation-preserving-replacement",
            ),
        )
        await coordinator.publish(editor, await ready(publishing, worker, checkpoint))
        receipt = await revisions.update_node(
            editor,
            command.model_copy(
                update={
                    "expected_revision": 1,
                    "changes": NodeChanges(tags=("reviewed",)),
                    "idempotency_key": "curation-metadata-only",
                }
            ),
        )
        await publish_curation(queue, coordinator, editor, receipt)
    node = await GraphService(queue).get_node(editor, job.kg_id, alpha)
    assert node.name == "Reviewed Alpha" and node.tags == ("reviewed",)
    assert {item.version_id for item in node.observations} == {successor.version_id}
    assert all(item.name == "Alpha" for item in node.observations)
    faults["embedding_inputs"].clear()
    receipt = await revisions.update_node(
        editor,
        command.model_copy(
            update={
                "expected_revision": 2,
                "changes": NodeChanges(aliases=("Reviewed alias",)),
                "idempotency_key": "curation-embedded-alias",
            }
        ),
    )
    await publish_curation(queue, coordinator, editor, receipt)
    assert len(faults["embedding_inputs"]) == 1
    assert "Reviewed alias" in faults["embedding_inputs"][0]
    node = await GraphService(queue).get_node(editor, job.kg_id, alpha)
    assert node.aliases == ("Reviewed alias",) and node.name == "Reviewed Alpha"


async def test_public_override_never_copies_private_default_fields(material_service, team):
    from test_entity_resolution import TEXT
    from test_publication import make_publishing

    from domain_kg.errors import DomainError
    from domain_kg.identity.models import Action
    from domain_kg.materials.models import AddMaterial

    fixture = await make_publishing(material_service, team, restricted=True)
    _, editor, queue, coordinator, _, _ = fixture
    source, job, checkpoint = await prepare(
        fixture,
        command=AddMaterial(
            kg_id=editor.kg_ids[0],
            title="Restricted source",
            inline_text=TEXT,
            acl_mode="restricted",
            idempotency_key="curation-private-source",
        ),
    )
    await coordinator.publish(editor, await ready(fixture, job, checkpoint))
    alpha = await alpha_id(queue, editor, job.kg_id)
    revisions = RevisionService(queue, coordinator.providers)
    command = NodeUpdate(
        kg_id=job.kg_id,
        entity_id=alpha,
        expected_revision=0,
        curation_scope="kg_public",
        changes=NodeChanges(description="Public explanation"),
        reason="Explicitly publish this description only.",
        idempotency_key="curation-public-field",
    )
    with pytest.raises(DomainError):
        await revisions.update_node(editor, command)
    reader = await queue.policy.resolve_access(team[2]["reader"], [job.kg_id], Action.SEARCH)
    with pytest.raises(DomainError):
        await GraphService(queue).get_node(reader, job.kg_id, alpha)
    owner = await queue.policy.resolve_access(team[2]["owner"], [job.kg_id], Action.CURATE)
    receipt = await revisions.update_node(owner, command)
    await publish_curation(queue, coordinator, owner, receipt)
    node = await GraphService(queue).get_node(reader, job.kg_id, alpha)
    assert node.name == str(alpha) and node.observations == ()
    assert node.descriptions == ("Public explanation",)
    assert not node.aliases and not node.entity_types and node.support_count == 0
    assert source.material_id not in {
        material for manual in node.curations for material in manual.material_ids
    }
    for mode in ("lexical", "vector", "graph", "deep"):
        result = await RetrievalService(queue, coordinator.providers).search(
            reader, SearchRequest(kg_ids=(job.kg_id,), query="Public explanation", mode=mode)
        )
        assert result.results and all(hit.kind == "manual" for hit in result.results)
        assert all(
            hit.material_id is None and "Alpha" not in hit.text and "阿尔法" not in hit.text
            for hit in result.results
        )
    filtered = await RetrievalService(queue, coordinator.providers).search(
        reader,
        SearchRequest(
            kg_ids=(job.kg_id,),
            query="Public explanation",
            mode="lexical",
            filters=SearchFilters(material_ids=(source.material_id,)),
        ),
    )
    assert filtered.results == ()


async def test_independent_source_scopes_have_independent_revision_checks(publishing):
    _, editor, queue, coordinator, _, _ = publishing
    sources = []
    for key in ("cas-a", "cas-b"):
        receipt, job, checkpoint = await prepare(publishing, key=key)
        await coordinator.publish(editor, await ready(publishing, job, checkpoint))
        sources.append(receipt)
    alpha = await alpha_id(queue, editor, job.kg_id)
    revisions = RevisionService(queue, coordinator.providers)
    for index, source in enumerate(sources):
        receipt = await revisions.update_node(
            editor,
            NodeUpdate(
                kg_id=job.kg_id,
                entity_id=alpha,
                expected_revision=0,
                source_ids=(source.material_id,),
                changes=NodeChanges(name=f"Scope {index} Alpha"),
                reason="Separate source ownership",
                idempotency_key=f"independent-{index}",
            ),
        )
        await publish_curation(queue, coordinator, editor, receipt)
    graph = GraphService(queue)
    for index, source in enumerate(sources):
        node = await graph.get_node(
            editor, job.kg_id, alpha, SearchFilters(material_ids=(source.material_id,))
        )
        assert node.name == f"Scope {index} Alpha"
        assert {manual.revision for manual in node.curations} == {1}
