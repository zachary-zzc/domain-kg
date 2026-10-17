"""Fresh-KG remapping changes typed identities, never evidence strings or authority."""

from datetime import UTC, datetime
from uuid import uuid4, uuid5

import pytest

from domain_kg.errors import DomainError
from domain_kg.graph.curation_models import ManualNode, NodeFields
from domain_kg.graph.identity_models import IdentityGroup, ManualIdentity
from domain_kg.graph.manual_relations import ManualRelation, RelationFields
from domain_kg.graph.models import AssertionRecord, EntityRecord, SupportRecord
from domain_kg.ingestion.models import SourceLocation, Span
from domain_kg.ingestion.resolution import fingerprint
from domain_kg.ingestion.resolved import ResolvedEntity
from domain_kg.models import ManualProvenance, Namespace, ScopedRef, SourceVersion


def example():
    source = Namespace(org_id=uuid4(), kg_id=uuid4())
    target = Namespace(org_id=source.org_id, kg_id=uuid4())
    version = SourceVersion(**source.model_dump(), material_id=uuid4(), version_id=uuid4())
    text = f"literal {source.kg_id} and {target.kg_id}"
    span = Span(
        location=SourceLocation(kind="text", block_id=uuid4()), start=0, end=len(text), quote=text
    )
    a, b = uuid4(), uuid4()
    observations = tuple(
        ResolvedEntity(
            **version.model_dump(),
            id=uuid4(),
            chunk_id=uuid4(),
            entity=ScopedRef(**source.model_dump(), id=value),
            name=text,
            entity_type="concept",
            description=text,
            identifier=identifier,
            spans=(span,),
        )
        for value, identifier in ((a, "  café  "), (b, None))
    )
    assertion = AssertionRecord(
        **source.model_dump(),
        id=uuid4(),
        subject=observations[0].entity,
        target=observations[1].entity,
        predicate="related_to",
        qualifiers=(("note", text),),
    )
    curation = ScopedRef(**source.model_dump(), id=uuid4())
    return source, target, version, observations, assertion, curation


def remapper(example_values):
    from domain_kg.operations.remapping import RestoreRemapper

    source, target, version, observations, assertion, curation = example_values
    return RestoreRemapper(
        source,
        target,
        versions=(version,),
        curations=(curation,),
        entities=tuple(EntityRecord(**o.entity.model_dump()) for o in observations),
        observations=observations,
        assertions=(assertion,),
    )


def test_native_identities_recompute_and_text_source_locations_stay_exact():
    values = example()
    source, target, version, observations, assertion, _ = values
    mapping = remapper(values)
    a, b = (mapping.observation(item) for item in observations)
    assert a.entity.id == uuid5(source.org_id, f"{target.kg_id}/entity/identifier/concept/café")
    assert b.entity.id != observations[1].entity.id
    assert a.name == observations[0].name and a.spans == observations[0].spans
    assert a.description == observations[0].description
    assert (a.material_id, a.version_id, a.id, a.chunk_id) == (
        version.material_id,
        version.version_id,
        observations[0].id,
        observations[0].chunk_id,
    )
    fact = mapping.assertion(assertion)
    digest = fingerprint(
        dict(
            subject=str(a.entity.id),
            target=str(b.entity.id),
            predicate=assertion.predicate,
            polarity=assertion.polarity,
            qualifiers=assertion.qualifiers,
        )
    )
    assert fact.id == uuid5(target.kg_id, f"assertion/{digest}")
    assert fact.qualifiers == assertion.qualifiers
    assert mapping.source(version).kg_id == target.kg_id
    assert remapper(values).assertion(assertion) == fact


def test_manual_support_and_identity_history_keep_independent_ownership():
    values = example()
    _, target, _, observations, fact, curation = values
    mapping = remapper(values)
    provenance = ManualProvenance(actor="historical actor", reason=observations[0].name)
    support = SupportRecord(
        **curation.model_dump(),
        assertion=ScopedRef(org_id=fact.org_id, kg_id=fact.kg_id, id=fact.id),
        status="manual",
        curation=curation,
        provenance=provenance,
    )
    relation = ManualRelation(
        curation=curation,
        logical_assertion=ScopedRef(org_id=fact.org_id, kg_id=fact.kg_id, id=fact.id),
        fields=RelationFields(
            subject_id=fact.subject.id, target_id=fact.target.id, description=provenance.reason
        ),
        fact=fact,
        support=support,
        provenance=provenance,
        revision=1,
        created_at=datetime.now(UTC),
        public=True,
    )
    result = mapping.manual_relation(relation)
    assert result.support.version is None and result.support.spans == ()
    assert result.support.status == "manual" and result.support.provenance == provenance
    assert result.fact.subject.id == result.fields.subject_id
    assert result.logical_assertion == ScopedRef(
        org_id=result.fact.org_id, kg_id=result.fact.kg_id, id=result.fact.id
    )
    node = ManualNode(
        curation=curation,
        entity=fact.subject,
        changes=NodeFields(description=provenance.reason),
        provenance=provenance,
        revision=1,
        created_at=datetime.now(UTC),
        public=True,
    )
    assert mapping.manual_node(node).changes == node.changes
    identity = ManualIdentity(
        curation=curation,
        target=fact.subject,
        groups=(
            IdentityGroup(
                canonical_id=fact.subject.id, member_ids=(fact.subject.id, fact.target.id)
            ),
        ),
        provenance=provenance,
        revision=1,
        created_at=datetime.now(UTC),
        public=True,
    )
    restored = mapping.manual_identity(identity)
    assert restored.target.kg_id == target.kg_id and restored.provenance == provenance
    assert restored.groups[0].canonical_id == result.fact.subject.id
    assert set(restored.groups[0].member_ids) == {result.fact.subject.id, result.fact.target.id}


def test_remapping_rejects_unknown_and_cross_namespace_references():
    from domain_kg.operations.remapping import RestoreRemapper

    values = example()
    source, _, version, observations, fact, _ = values
    with pytest.raises(DomainError):
        RestoreRemapper(source, source)
    with pytest.raises(DomainError):
        RestoreRemapper(source, Namespace(org_id=uuid4(), kg_id=uuid4()))
    mapping = remapper(values)
    for reference in (
        ScopedRef(**source.model_dump(), id=uuid4()),
        ScopedRef(org_id=source.org_id, kg_id=uuid4(), id=fact.subject.id),
    ):
        with pytest.raises(DomainError):
            mapping.entity_ref(reference)
    unknown = version.model_copy(update={"material_id": uuid4()})
    with pytest.raises(DomainError):
        mapping.source(unknown)
    invalid = fact.model_copy(update={"target": ScopedRef(**source.model_dump(), id=uuid4())})
    with pytest.raises(DomainError):
        RestoreRemapper(
            source,
            values[1],
            versions=(version,),
            observations=observations,
            entities=tuple(EntityRecord(**o.entity.model_dump()) for o in observations),
            assertions=(invalid,),
        )


def test_remapping_rejects_uninventoried_or_changed_observation():
    values = example()
    mapping = remapper(values)
    item = values[3][0]
    for changed in (
        item.model_copy(update={"id": uuid4()}),
        item.model_copy(update={"description": "tampered"}),
    ):
        with pytest.raises(DomainError):
            mapping.observation(changed)


def test_imported_canonical_ids_match_a_fresh_parse_and_keep_unverified_status():
    from domain_kg.imports.legacy import parse_legacy
    from domain_kg.operations.remapping import RestoreRemapper

    source, target, version, _, _, _ = example()
    body = (
        b"entity1,entity1_type,relation,entity2,entity2_type,pdf_name,source\n"
        b"Alpha,concept,related_to,Beta,concept,demo,fixture\n"
    )
    graph = parse_legacy(source, "csv", body, origin="owned export")
    fresh = parse_legacy(target, "csv", body, origin="owned export")
    entities = tuple(EntityRecord(**source.model_dump(), id=e.entity_id) for e in graph.entities)
    facts = tuple(
        AssertionRecord(
            **source.model_dump(),
            id=r.assertion_id,
            subject=ScopedRef(**source.model_dump(), id=r.subject_id),
            target=ScopedRef(**source.model_dump(), id=r.target_id),
            predicate=r.predicate,
            polarity=r.polarity,
        )
        for r in graph.relations
    )
    mapping = RestoreRemapper(
        source,
        target,
        versions=(version,),
        entities=entities,
        assertions=facts,
        legacy_graphs=(graph,),
    )
    assert {mapping.entity_ref(e).id for e in entities} == {e.entity_id for e in fresh.entities}
    assert {mapping.assertion(f).id for f in facts} == {r.assertion_id for r in fresh.relations}
    support = SupportRecord(
        **source.model_dump(),
        id=uuid4(),
        assertion=ScopedRef(**source.model_dump(), id=facts[0].id),
        version=ScopedRef(**source.model_dump(), id=version.version_id),
        status="imported_unverified",
    )
    restored = mapping.support(support)
    assert restored.status == "imported_unverified" and restored.spans == ()
    assert restored.version.kg_id == target.kg_id


def test_projection_bodies_rebind_all_typed_refs_without_copying_vectors():
    from domain_kg.ingestion.resolved import ResolvedAssertion
    from domain_kg.revisions.projections import ProjectionRow

    values = example()
    source, target, version, observations, fact, _ = values
    mapping = remapper(values)
    support = SupportRecord(
        **source.model_dump(),
        id=uuid4(),
        assertion=ScopedRef(**source.model_dump(), id=fact.id),
        version=ScopedRef(**source.model_dump(), id=version.version_id),
        status="verified",
        spans=observations[0].spans,
    )
    original = ProjectionRow(
        **source.model_dump(),
        id=support.id,
        source=version,
        kind="support",
        assertion=ResolvedAssertion(
            assertion=fact,
            support=support,
            subject_observation_id=observations[0].id,
            target_observation_id=observations[1].id,
        ),
        subject_observation=observations[0],
        target_observation=observations[1],
        vector=(1.0, 0.0, 0.0),
    )
    restored = mapping.projection_body(original)
    assert restored.kg_id == target.kg_id and restored.source == mapping.source(version)
    assert restored.subject_observation.entity == restored.assertion.assertion.subject
    assert restored.target_observation.entity == restored.assertion.assertion.target
    assert restored.assertion.support.spans == support.spans
    assert restored.text == original.text
    assert "vector" not in restored.model_dump()
    assert original.kg_id == source.kg_id


def test_conflicting_identifier_history_cannot_silently_merge_or_reassign_identity():
    from domain_kg.operations.remapping import RestoreRemapper

    source, target, version, observations, _, _ = example()
    changed = observations[0].model_copy(update={"id": uuid4(), "identifier": "different"})
    with pytest.raises(DomainError):
        RestoreRemapper(
            source,
            target,
            versions=(version,),
            entities=tuple(EntityRecord(**o.entity.model_dump()) for o in observations),
            observations=(*observations, changed),
        )
