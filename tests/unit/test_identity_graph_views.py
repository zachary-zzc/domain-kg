"""Mapped graph views preserve original source observations, assertions and spans."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from domain_kg.errors import DomainError
from domain_kg.graph.identity_models import IdentityGroup, ManualIdentity
from domain_kg.graph.models import AssertionRecord, SupportRecord
from domain_kg.graph.repository import GraphData, GraphRepository
from domain_kg.identity.models import AccessContext, Action, KGGrant, Principal
from domain_kg.ingestion.models import SourceLocation, Span
from domain_kg.ingestion.resolved import ResolvedAssertion, ResolvedEntity
from domain_kg.models import ManualProvenance, Namespace, ScopedRef, SourceVersion
from domain_kg.retrieval.models import SearchFilters
from domain_kg.revisions.models import PublishedView, ReadScope
from domain_kg.revisions.projections import AcceptedProjection, ProjectionBody


def graph_fixture(filters=None):
    ns = Namespace(org_id=uuid4(), kg_id=uuid4())
    alpha, beta, gamma, a, b = (uuid4() for _ in range(5))
    text = "Alpha and Beta; Beta uses Gamma."
    rows = []
    sources = []
    for material in (a, b):
        source = SourceVersion(**ns.model_dump(), material_id=material, version_id=uuid4())
        sources.append(source)
        location = SourceLocation(kind="text", block_id=uuid4())
        observations = []
        for entity, name in ((alpha, "Alpha"), (beta, "Beta"), (gamma, "Gamma")):
            start = text.index(name)
            item = ResolvedEntity(
                **source.model_dump(),
                id=uuid4(),
                chunk_id=source.version_id,
                entity=ScopedRef(**ns.model_dump(), id=entity),
                name=name,
                entity_type="concept",
                description=name,
                spans=(Span(location=location, start=start, end=start + len(name), quote=name),),
            )
            observations.append(item)
            rows.append(
                ProjectionBody(
                    **ns.model_dump(),
                    id=item.id,
                    kind="observation",
                    source=source,
                    observation=item,
                )
            )
        fact = AssertionRecord(
            **ns.model_dump(),
            id=uuid4(),
            subject=observations[1].entity,
            target=observations[2].entity,
            predicate="uses",
        )
        support = SupportRecord(
            **ns.model_dump(),
            id=uuid4(),
            assertion=ScopedRef(**ns.model_dump(), id=fact.id),
            version=ScopedRef(**ns.model_dump(), id=source.version_id),
            status="verified",
            spans=(Span(location=location, start=0, end=len(text), quote=text),),
        )
        resolved = ResolvedAssertion(
            assertion=fact,
            support=support,
            subject_observation_id=observations[1].id,
            target_observation_id=observations[2].id,
        )
        rows.append(
            ProjectionBody(
                **ns.model_dump(),
                id=support.id,
                kind="support",
                source=source,
                assertion=resolved,
                subject_observation=observations[1],
                target_observation=observations[2],
            )
        )
    identity = ManualIdentity(
        curation=ScopedRef(**ns.model_dump(), id=uuid4()),
        target=ScopedRef(**ns.model_dump(), id=alpha),
        groups=(IdentityGroup(canonical_id=alpha, member_ids=(alpha, beta)),),
        public=False,
        material_ids=(a,),
        revision=1,
        created_at=datetime.now(UTC),
        provenance=ManualProvenance(actor="fixture-editor", reason="Reviewed identity"),
    )
    generation = uuid4()
    bindings = [
        AcceptedProjection(
            **ns.model_dump(),
            source=source,
            attempt_id=uuid4(),
            manifest_id=uuid4(),
            content_hash="0" * 64,
            generation=generation,
            chunk_artifact_id=uuid4(),
        )
        for source in sources
    ]
    bindings.append(
        AcceptedProjection(
            **ns.model_dump(),
            curation=identity.curation,
            attempt_id=uuid4(),
            manifest_id=uuid4(),
            content_hash="1" * 64,
            generation=generation,
        )
    )
    view = PublishedView(
        **ns.model_dump(),
        revision=1,
        generation=generation,
        manifest_id=uuid4(),
        sources=tuple(sources),
        curations=(identity.curation,),
        projections=tuple(bindings),
    )
    access = AccessContext(
        principal=Principal(issuer="https://fixture.invalid", subject="editor"),
        org_id=ns.org_id,
        kg_ids=(ns.kg_id,),
        policy_revision=1,
        grants=(KGGrant(kg_id=ns.kg_id, role="editor"),),
        action=Action.SEARCH,
    )
    scope = ReadScope(
        access=access,
        view=view,
        visible_sources=tuple(sources),
        visible_curations=(identity.curation,),
        visibility_revision=1,
        filters=filters or SearchFilters(),
    )
    return GraphData(scope, tuple(rows), (identity,)), alpha, beta, gamma, a, b, identity


def test_merged_node_combines_only_mapped_source_observations():
    data, alpha, beta, _, a, b, identity = graph_fixture()
    repository = GraphRepository(None, None)
    merged = repository.node(data, alpha)
    independent = repository.node(data, beta)
    assert merged.observation_count == 3 and merged.support_count == 1
    assert merged.name == "Alpha" and "Beta" in merged.aliases
    assert independent.observation_count == 1
    assert {item.material_id for item in independent.observations} == {b}
    assert {item.id for item in merged.observations if item.material_id == a}
    assert set(merged.identity_members) == {alpha, beta}
    assert identity.curation in {
        proof for item in merged.identity_mappings for proof in item.curations
    }


def test_effective_entity_filters_are_source_qualified():
    unfiltered, alpha, _, _, _, _, _ = graph_fixture()
    positive = GraphData(
        unfiltered.scope.model_copy(update={"filters": SearchFilters(entity_ids=(alpha,))}),
        unfiltered.rows,
        unfiltered.curations,
    )
    negative = GraphData(
        unfiltered.scope.model_copy(update={"filters": SearchFilters(exclude_entity_ids=(alpha,))}),
        unfiltered.rows,
        unfiltered.curations,
    )
    a = unfiltered.curations[0].material_ids[0]
    assert len(positive.supports) == 1 and positive.supports[0].source.material_id == a
    assert len(negative.supports) == 1 and negative.supports[0].source.material_id != a


def test_mapped_edge_retains_original_endpoints_and_manual_identity_witness():
    data, alpha, beta, gamma, a, _, identity = graph_fixture()
    row = next(item for item in data.supports if item.source.material_id == a)
    before = row.model_dump_json()
    edge = GraphRepository.edge(row, data=data)
    assert (edge.subject_id, edge.target_id) == (alpha, gamma)
    assert (edge.original_subject_id, edge.original_target_id) == (beta, gamma)
    assert edge.assertion_id == row.claim.assertion.id and edge.spans == row.claim.support.spans
    assert identity.curation in {
        proof for item in edge.identity_mappings for proof in item.curations
    }
    assert row.model_dump_json() == before


def test_filtered_group_retains_its_qualifying_original_observation_fields():
    data, alpha, _, _, a, b, _ = graph_fixture()
    filtered = GraphData(
        data.scope.model_copy(update={"filters": SearchFilters(entity_ids=(alpha,))}),
        data.rows,
        data.curations,
    )
    node = GraphRepository(None, None).node(filtered, alpha)
    assert node.name == "Alpha" and node.observation_count == 2
    assert {item.material_id for item in node.observations} == {a}
    assert b not in {item.material_id for item in node.observations}


@pytest.mark.parametrize("smuggle", [False, True])
async def test_alias_adjacency_selects_only_matching_source_support(smuggle):
    data, alpha, beta, _, a, b, _ = graph_fixture()
    arguments = []

    class Driver:
        async def execute_query(self, statement, **parameters):
            arguments.append(parameters)
            wanted = {str(beta)} if not smuggle else None
            rows = [
                row
                for row in data.supports
                if row.id
                and (
                    str(row.id) in parameters["support_ids"]
                    and {
                        str(row.claim.assertion.subject.id),
                        str(row.claim.assertion.target.id),
                    }.intersection(parameters["seed_ids"])
                    if wanted is not None
                    else row.source.material_id == b
                )
            ]
            return [{"id": str(row.id)} for row in rows][: parameters["limit"]], None, None

    class Router:
        async def for_graph(self, access, kg):
            return SimpleNamespace(driver=Driver())

    repository = GraphRepository(SimpleNamespace(router=Router()), None)
    if smuggle:
        with pytest.raises(DomainError):
            await repository.adjacent(data, (alpha,), 401)
    else:
        rows = await repository.adjacent(data, (alpha,), 401)
        assert len(rows) == 1 and rows[0].source.material_id == a
        assert str(beta) in arguments[0]["seed_ids"]


def test_unique_source_alias_redirects_to_current_representative():
    data, alpha, beta, _, a, _, _ = graph_fixture()
    source_a = next(source for source in data.scope.visible_sources if source.material_id == a)
    scoped = GraphData(
        data.scope.model_copy(
            update={"visible_sources": (source_a,), "filters": SearchFilters(material_ids=(a,))}
        ),
        tuple(row for row in data.rows if row.source.material_id == a),
        data.curations,
    )
    node = GraphRepository(None, None).node(scoped, beta)
    assert node is not None and node.id == alpha and node.requested_id == beta
    assert set(node.identity_members) == {alpha, beta}


def test_disagreeing_source_aliases_require_explicit_scope():
    data, _, beta, gamma, _, b, original = graph_fixture()
    second = ManualIdentity.model_validate(
        original.model_dump()
        | {
            "curation": original.curation.model_copy(update={"id": uuid4()}),
            "target": original.target.model_copy(update={"id": gamma}),
            "groups": (IdentityGroup(canonical_id=gamma, member_ids=(beta, gamma)),),
            "material_ids": (b,),
        }
    )
    binding = AcceptedProjection(
        org_id=second.curation.org_id,
        kg_id=second.curation.kg_id,
        curation=second.curation,
        generation=data.scope.view.generation,
        attempt_id=uuid4(),
        manifest_id=uuid4(),
        content_hash="2" * 64,
    )
    view = PublishedView.model_validate(
        data.scope.view.model_dump()
        | {
            "curations": (*data.scope.view.curations, second.curation),
            "projections": (*data.scope.view.projections, binding),
        }
    )
    scope = ReadScope.model_validate(
        data.scope.model_dump()
        | {
            "view": view,
            "visible_curations": (*data.scope.visible_curations, second.curation),
        }
    )
    changed = GraphData(scope, data.rows, (*data.curations, second))
    with pytest.raises(DomainError):
        GraphRepository(None, None).node(changed, beta)
