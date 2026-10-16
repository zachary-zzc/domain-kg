"""A changed meaning is a manual claim; independently verified originals survive."""
# ruff: noqa: F811

import pytest
from test_curation import alpha_id, publish_curation
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.graph.service import GraphService
from domain_kg.retrieval.models import SearchFilters, SearchRequest, SubgraphRequest
from domain_kg.retrieval.service import RetrievalService
from domain_kg.revisions.models import RelationChanges, RelationUpdate
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


async def test_semantic_relation_edit_keeps_original_support_and_manual_provenance(publishing):
    _, editor, queue, coordinator, _, _ = publishing
    sources = []
    for key in ("relation-source-a", "relation-source-b"):
        source, job, checkpoint = await prepare(publishing, key=key)
        await coordinator.publish(editor, await ready(publishing, job, checkpoint))
        sources.append(source)
    graph = GraphService(queue)
    alpha = await alpha_id(queue, editor, job.kg_id)
    scope = await graph.resolver.resolve(
        editor, SearchRequest(kg_ids=editor.kg_ids, query="Alpha", mode="graph"), job.kg_id
    )
    rows = (await graph.repository.open(scope)).supports
    original = rows[0].assertion.assertion
    before = [row.model_dump(mode="json") for row in rows]
    command = RelationUpdate(
        kg_id=job.kg_id,
        assertion_id=original.id,
        expected_revision=0,
        source_ids=(sources[0].material_id,),
        changes=RelationChanges(
            predicate="related_to", description="Explicit human relationship correction"
        ),
        reason="The relationship meaning was reviewed.",
        idempotency_key="manual-relation-meaning",
    )
    revisions = RevisionService(queue, coordinator.providers)
    receipt = await revisions.update_relation(editor, command)
    assert await revisions.update_relation(editor, command) == receipt
    await publish_curation(queue, coordinator, editor, receipt)
    current = await graph.subgraph(editor, SubgraphRequest(kg_id=job.kg_id, seed_ids=(alpha,)))
    assert sum(edge.predicate == "uses" for edge in current.edges) == 2
    changed = [edge for edge in current.edges if edge.predicate == "related_to"]
    assert len(changed) == 1 and changed[0].source is None and changed[0].spans == ()
    assert changed[0].curation_revision_id == receipt.revision_id
    assert changed[0].provenance.reason == command.reason
    assert changed[0].assertion_id != original.id
    source_b = await graph.subgraph(
        editor,
        SubgraphRequest(
            kg_id=job.kg_id,
            seed_ids=(alpha,),
            filters=SearchFilters(material_ids=(sources[1].material_id,)),
        ),
    )
    assert len(source_b.edges) == 1 and source_b.edges[0].predicate == "uses"
    scoped = await graph.resolver.resolve(
        editor, SearchRequest(kg_ids=editor.kg_ids, query="Alpha", mode="graph"), job.kg_id
    )
    source_rows = [
        row.model_dump(mode="json")
        for row in (await graph.repository.open(scoped)).supports
        if row.source
    ]
    assert sorted(source_rows, key=str) == sorted(before, key=str)
    search = RetrievalService(queue, coordinator.providers)
    for mode in ("vector", "lexical", "graph", "deep"):
        response = await search.search(
            editor,
            SearchRequest(
                kg_ids=editor.kg_ids,
                query="related_to",
                mode=mode,
                filters=SearchFilters(
                    material_ids=(sources[0].material_id,), predicates=("related_to",)
                ),
            ),
        )
        assert response.results and all(hit.kind == "manual" for hit in response.results)
        assert all(
            hit.curation_revision_id == receipt.revision_id
            and hit.version_id is None
            and hit.chunk_id is None
            for hit in response.results
        )


async def test_relation_rollback_preserves_later_tags_and_removes_only_manual_meaning(
    publishing, team, monkeypatch
):
    from domain_kg.identity.models import Action
    from domain_kg.revisions.models import RollbackRevision

    _, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing, key="rollback-relation-source")
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    graph = GraphService(queue)
    scope = await graph.resolver.resolve(
        editor, SearchRequest(kg_ids=editor.kg_ids, query="Alpha", mode="graph"), job.kg_id
    )
    data = await graph.repository.open(scope)
    original = data.supports[0].assertion.assertion
    revisions = RevisionService(queue, coordinator.providers)
    command = RelationUpdate(
        kg_id=job.kg_id,
        assertion_id=original.id,
        expected_revision=0,
        source_ids=(source.material_id,),
        changes=RelationChanges(predicate="related_to"),
        reason="Review the meaning",
        idempotency_key="relation-to-rollback",
    )
    first = await revisions.update_relation(editor, command)
    await publish_curation(queue, coordinator, editor, first)

    async def forbidden(*args, **kwargs):
        pytest.fail("tags and withdrawing a manual claim require no embedding")

    with monkeypatch.context() as patch:
        patch.setattr(coordinator.providers, "embeddings", forbidden)
        second = await revisions.update_relation(
            editor,
            command.model_copy(
                update={
                    "expected_revision": 1,
                    "changes": RelationChanges(tags=("reviewed",)),
                    "idempotency_key": "relation-tags-only",
                }
            ),
        )
        await publish_curation(queue, coordinator, editor, second)
        updated_scope = await graph.resolver.resolve(
            editor, SearchRequest(kg_ids=editor.kg_ids, query="Alpha", mode="graph"), job.kg_id
        )
        updated = await graph.repository.open(updated_scope)
        current_claim = next(
            row.claim.assertion.id for row in updated.supports if row.manual_relation
        )
        first_record = await queue.database.curation_revisions.find_one(
            {"org_id": str(editor.org_id), "kg_id": str(job.kg_id), "id": str(first.revision_id)}
        )
        from domain_kg.revisions.curation_publication import directive

        assert current_claim == directive(first_record).fact.id
        owner = await queue.policy.resolve_access(team[2]["owner"], [job.kg_id], Action.CURATE)
        compensation = await revisions.rollback(
            owner,
            RollbackRevision(
                kg_id=job.kg_id,
                revision_id=first.revision_id,
                expected_current_revision=2,
                reason="Withdraw the changed meaning",
                idempotency_key="compensate-relation",
            ),
        )
        await publish_curation(queue, coordinator, owner, compensation)
    current = await graph.subgraph(
        editor, SubgraphRequest(kg_id=job.kg_id, seed_ids=(original.subject.id,))
    )
    assert len(current.edges) == 1 and current.edges[0].predicate == "uses"
    ns = {"org_id": str(editor.org_id), "kg_id": str(job.kg_id)}
    record = await queue.database.curation_revisions.find_one(
        ns | {"id": str(compensation.revision_id)}
    )
    assert record["after"] == {"tags": ["reviewed"]}
    assert record["rollback_of"] == str(first.revision_id)
    assert (await queue.database.curation_revisions.find_one(ns | {"id": str(first.revision_id)}))[
        "after"
    ] == {"predicate": "related_to"}


async def test_public_relation_requires_explicit_endpoints_and_never_private_display(
    publishing, team
):
    from domain_kg.errors import DomainError, ErrorCode
    from domain_kg.identity.models import Action

    materials, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing, key="public-relation-source")
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    graph = GraphService(queue)
    scope = await graph.resolver.resolve(
        editor, SearchRequest(kg_ids=editor.kg_ids, query="Alpha", mode="graph"), job.kg_id
    )
    data = await graph.repository.open(scope)
    original = data.supports[0].assertion.assertion
    command = RelationUpdate(
        kg_id=job.kg_id,
        assertion_id=original.id,
        expected_revision=0,
        curation_scope="kg_public",
        changes=RelationChanges(predicate="related_to"),
        reason="Public claim",
        idempotency_key="public-relation",
    )
    revisions = RevisionService(queue, coordinator.providers)
    owner = await queue.policy.resolve_access(team[2]["owner"], [job.kg_id], Action.CURATE)
    with pytest.raises(DomainError) as error:
        await revisions.update_relation(owner, command)
    assert error.value.code == ErrorCode.VALIDATION_ERROR
    receipt = await revisions.update_relation(
        owner,
        command.model_copy(
            update={
                "changes": RelationChanges(
                    subject_id=original.subject.id,
                    target_id=original.target.id,
                    predicate="related_to",
                    description="Explicit public claim",
                )
            }
        ),
    )
    await publish_curation(queue, coordinator, owner, receipt)
    search = RetrievalService(queue, coordinator.providers)
    response = await search.search(
        editor,
        SearchRequest(
            kg_ids=editor.kg_ids,
            query="Explicit public claim",
            mode="lexical",
            filters=SearchFilters(predicates=("related_to",)),
        ),
    )
    assert response.results and all(hit.kind == "manual" for hit in response.results)
    for hit in response.results:
        assert hit.material_id is None and "Alpha" not in hit.text and "Beta" not in hit.text
    ns = {"org_id": str(editor.org_id), "kg_id": str(job.kg_id)}
    record = await queue.database.curation_revisions.find_one(ns | {"id": str(receipt.revision_id)})
    assert record["base_fact"] is None and record["material_ids"] == []

    from domain_kg.materials.lifecycle import MaterialLifecycle
    from domain_kg.materials.models import RemoveMaterial

    await MaterialLifecycle(materials, queue).remove(
        editor,
        RemoveMaterial(
            kg_id=job.kg_id,
            material_id=source.material_id,
            action="delete",
            expected_revision=1,
            idempotency_key="public-relation-source-removed",
        ),
    )
    reader = await queue.policy.resolve_access(team[2]["reader"], [job.kg_id], Action.SEARCH)
    visible = await graph.subgraph(
        reader,
        SubgraphRequest(
            kg_id=job.kg_id,
            seed_ids=(original.subject.id,),
            filters=SearchFilters(predicates=("related_to",)),
        ),
    )
    assert len(visible.edges) == 1 and visible.edges[0].status == "manual"
    assert len(visible.nodes) == 2
    assert all(node.name == str(node.id) and not node.observations for node in visible.nodes)
    for mode in ("vector", "lexical", "graph", "deep"):
        hits = await search.search(
            reader,
            SearchRequest(
                kg_ids=(job.kg_id,),
                query="related_to",
                mode=mode,
                filters=SearchFilters(predicates=("related_to",)),
            ),
        )
        assert hits.results and all(hit.kind == "manual" for hit in hits.results)
        assert all("Alpha" not in hit.text and "Beta" not in hit.text for hit in hits.results)


async def test_manual_relation_canonical_support_cannot_reference_another_kg(publishing):
    from uuid import uuid4

    from domain_kg.errors import DomainError

    _, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing, key="relation-foreign-support")
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    graph = GraphService(queue)
    scope = await graph.resolver.resolve(
        editor, SearchRequest(kg_ids=editor.kg_ids, query="Alpha", mode="graph"), job.kg_id
    )
    original = (await graph.repository.open(scope)).supports[0].assertion.assertion
    receipt = await RevisionService(queue, coordinator.providers).update_relation(
        editor,
        RelationUpdate(
            kg_id=job.kg_id,
            assertion_id=original.id,
            expected_revision=0,
            source_ids=(source.material_id,),
            changes=RelationChanges(predicate="related_to"),
            reason="Explicit reviewed relationship",
            idempotency_key="foreign-manual-support",
        ),
    )
    await publish_curation(queue, coordinator, editor, receipt)
    ns = {"org_id": str(editor.org_id), "kg_id": str(job.kg_id)}
    await queue.database.supports.update_one(
        ns | {"id": str(receipt.revision_id)},
        {"$set": {"assertion.kg_id": str(uuid4())}},
    )
    with pytest.raises(DomainError):
        await graph.subgraph(
            editor,
            SubgraphRequest(
                kg_id=job.kg_id,
                seed_ids=(original.subject.id,),
            ),
        )

    canonical = await queue.database.supports.find_one(ns | {"id": str(receipt.revision_id)})
    await queue.database.supports.update_one(
        ns | {"id": str(receipt.revision_id)},
        {"$set": {"assertion.kg_id": str(job.kg_id)}},
    )
    assert canonical["curation"]["id"] == str(receipt.revision_id)
    await queue.database.curation_access.update_one(
        ns | {"id": str(receipt.revision_id)},
        {"$set": {"public": True, "material_ids": []}},
    )
    with pytest.raises(DomainError):
        await graph.subgraph(
            editor,
            SubgraphRequest(
                kg_id=job.kg_id,
                seed_ids=(original.subject.id,),
            ),
        )
