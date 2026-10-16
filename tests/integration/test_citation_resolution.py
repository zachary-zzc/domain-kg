"""Citation resolvers use current accepted evidence and source permissions."""
# ruff: noqa: F811

from uuid import uuid4

import pytest
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.citations.service import CitationService
from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.materials.lifecycle import MaterialLifecycle
from domain_kg.materials.models import RemoveMaterial
from domain_kg.retrieval.models import SearchRequest
from domain_kg.retrieval.service import RetrievalService

pytestmark = pytest.mark.storage


async def test_source_citations_match_spans_and_deny_unknown_scopes_and_removed_sources(
    publishing, team
):
    materials, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    reader = await queue.policy.resolve_access(team[2]["reader"], [job.kg_id], Action.READ)
    response = await RetrievalService(queue, coordinator.providers).search(
        reader, SearchRequest(kg_ids=(job.kg_id,), query="Alpha", mode="lexical")
    )
    result = response.results[0]
    service = CitationService(queue)
    citation = await service.resolve(reader, job.kg_id, result.citation_ids[0])
    assert citation.kind == "source" and citation.text == result.text
    assert citation.material_id == source.material_id
    assert citation.version_id == source.version_id and citation.chunk_id == result.chunk_id
    assert citation.spans and all(span.quote in citation.text for span in citation.spans)
    assert citation.original_url.endswith(f"/versions/{source.version_id}/original")
    assert citation.resolver_url.startswith("/api/v1/citations/")
    assert (
        "bucket" not in citation.model_dump_json() and "object_id" not in citation.model_dump_json()
    )
    for kg, identifier in ((uuid4(), citation.id), (job.kg_id, uuid4())):
        with pytest.raises(DomainError) as denied:
            await service.resolve(reader, kg, identifier)
        assert denied.value.code == ErrorCode.RESOURCE_NOT_ACCESSIBLE
    await MaterialLifecycle(materials, queue).remove(
        editor,
        RemoveMaterial(
            kg_id=job.kg_id,
            material_id=source.material_id,
            action="delete",
            expected_revision=1,
            idempotency_key="citation-remove",
        ),
    )
    with pytest.raises(DomainError) as removed:
        await service.resolve(reader, job.kg_id, citation.id)
    assert removed.value.code == ErrorCode.RESOURCE_NOT_ACCESSIBLE


async def test_manual_citation_preserves_human_provenance_without_source_spans(publishing):
    from test_curation import alpha_id, publish_curation

    from domain_kg.revisions.models import NodeChanges, NodeUpdate
    from domain_kg.revisions.service import RevisionService

    _, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    alpha = await alpha_id(queue, editor, job.kg_id)
    change = await RevisionService(queue, coordinator.providers).update_node(
        editor,
        NodeUpdate(
            kg_id=job.kg_id,
            entity_id=alpha,
            expected_revision=0,
            curation_scope="source",
            source_ids=(source.material_id,),
            changes=NodeChanges(name="Human reviewed Alpha"),
            reason="Citation fixture review",
            idempotency_key="citation-manual",
        ),
    )
    await publish_curation(queue, coordinator, editor, change)
    response = await RetrievalService(queue, coordinator.providers).search(
        editor, SearchRequest(kg_ids=(job.kg_id,), query="Human reviewed Alpha", mode="lexical")
    )
    candidate = next(item for item in response.results if item.kind == "manual")
    citation = await CitationService(queue).resolve(editor, job.kg_id, candidate.citation_ids[0])
    assert citation.kind == "manual" and citation.text == candidate.text
    assert citation.provenance == candidate.provenance
    assert citation.curation_revision_id == change.revision_id
    assert not citation.spans and citation.original_url is None
    assert citation.version_id is None and citation.chunk_id is None
