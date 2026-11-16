"""Content-derived facts survive real publication, retrieval and manual curation."""

from uuid import uuid4

import pytest
from test_curation import publish_curation
from test_publication import make_publishing, prepare, ready

from domain_kg.errors import DomainError
from domain_kg.graph.schema import schema_for, schema_for_kg
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.ingestion.models import ExtractedAssertion, ExtractedEntity, ExtractionBatch
from domain_kg.materials.models import AddMaterial
from domain_kg.models import Namespace
from domain_kg.retrieval.models import SearchRequest
from domain_kg.retrieval.service import RetrievalService
from domain_kg.revisions.models import NodeChanges, NodeUpdate, RelationChanges, RelationUpdate
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


def facts(chunk, profile_id, *, connected=True):
    schema = schema_for(Namespace(org_id=chunk.org_id, kg_id=chunk.kg_id), "general")
    return ExtractionBatch(
        org_id=chunk.org_id,
        kg_id=chunk.kg_id,
        material_id=chunk.material_id,
        version_id=chunk.version_id,
        chunk_id=chunk.id,
        schema_id=schema.id,
        schema_revision=schema.revision,
        provider_profile_id=profile_id,
        entities=tuple(
            ExtractedEntity(local_id=key, name=name, entity_type=kind, spans=chunk.spans)
            for key, name, kind in (
                ("panel", "PanelX", "assay/panel"),
                ("specimen", "Saliva", "specimen"),
                ("orphan", "Flyer", "document"),
            )
        ),
        assertions=tuple(
            ExtractedAssertion(
                subject="panel",
                target="specimen",
                predicate=predicate,
                confidence=0.9,
                spans=chunk.spans,
            )
            for predicate in (("requires_sample", "analyzes") if connected else ())
        ),
    )


@pytest.mark.parametrize("connected", [True, False])
@pytest.mark.parametrize("semantics", ["legacy", "lightrag_general_v1"])
async def test_published_graph_has_no_orphans_and_keeps_searchable_source(
    material_service, team, connected, semantics
):
    publishing = await make_publishing(
        material_service,
        team,
        extraction_factory=lambda chunk, profile: facts(chunk, profile, connected=connected),
        profile_overrides={"extraction_semantics": semantics},
    )
    _, access, queue, coordinator, _, _ = publishing
    receipt, job, checkpoint = await prepare(
        publishing,
        command=AddMaterial(
            kg_id=access.kg_ids[0],
            title="Explicit facts and an unlinked mention",
            inline_text="PanelX requires Saliva and analyzes Saliva. Flyer.",
            idempotency_key="open-vocabulary",
        ),
    )
    await coordinator.publish(access, await ready(publishing, job, checkpoint))
    graph = GraphService(queue)
    request = SearchRequest(kg_ids=access.kg_ids, query="Flyer", mode="lexical")
    scope = await graph.resolver.resolve(access, request, job.kg_id)
    data = await graph.repository.open(scope)  # Checks accepted Neo4j/Qdrant receipts.
    assert {item.name for item in data.observations} == (
        {"PanelX", "Saliva"} if connected else set()
    )
    assert {row.assertion.assertion.predicate for row in data.supports} == (
        {"requires_sample", "analyzes"} if connected else set()
    )
    search = await RetrievalService(queue, coordinator.providers).search(access, request)
    assert search.results and "Flyer" in search.results[0].text
    with pytest.raises(DomainError):
        await queue.policy.resolve_access(team[2]["denied"], [job.kg_id], Action.SEARCH)
    if not connected:
        return

    revisions = RevisionService(queue, coordinator.providers)
    panel = next(item.entity.id for item in data.observations if item.name == "PanelX")
    node_edit = await revisions.update_node(
        access,
        NodeUpdate(
            kg_id=job.kg_id,
            entity_id=panel,
            expected_revision=0,
            source_ids=(receipt.material_id,),
            changes=NodeChanges(entity_type="diagnostic_panel"),
            reason="Review the content-derived type",
            idempotency_key="open-type-edit",
        ),
    )
    await publish_curation(queue, coordinator, access, node_edit)
    assert "diagnostic_panel" in (await graph.get_node(access, job.kg_id, panel)).entity_types
    assertion = data.supports[0].assertion.assertion
    relation_edit = await revisions.update_relation(
        access,
        RelationUpdate(
            kg_id=job.kg_id,
            assertion_id=assertion.id,
            expected_revision=0,
            source_ids=(receipt.material_id,),
            changes=RelationChanges(predicate="accepts_specimen"),
            reason="Review the content-derived relation",
            idempotency_key="open-relation-edit",
        ),
    )
    await publish_curation(queue, coordinator, access, relation_edit)
    updated = await graph.repository.open(await graph.resolver.resolve(access, request, job.kg_id))
    assert any(row.manual_relation for row in updated.supports)


async def test_stored_legacy_schema_remains_closed(material_service, team, mongo_db):
    _, kg = material_service
    _, policy, principals, _, org, _ = team
    access = await policy.resolve_access(principals["owner"], [kg], Action.CONFIGURE)
    legacy = {
        "org_id": str(org),
        "kg_id": str(kg),
        "id": str(uuid4()),
        "revision": 1,
        "entity_types": ["concept"],
        "predicates": [],
    }
    await mongo_db.kg_schemas.insert_one(dict(legacy))
    schema = await schema_for_kg(policy, access, kg)
    assert schema.model_dump(mode="json") == legacy
    assert not schema.allows_entity_type("assay_panel")
    assert not schema.allows_predicate("requires_sample")
