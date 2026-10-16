"""Published real-store topology: visibility and filters apply before expansion limits."""

from uuid import UUID

import pytest
from pydantic import ValidationError
from test_publication import make_publishing, prepare, ready

from domain_kg.graph.schema import schema_for
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.ingestion.models import ExtractedAssertion, ExtractedEntity, ExtractionBatch
from domain_kg.materials.models import AddMaterial
from domain_kg.models import Namespace
from domain_kg.retrieval.models import SearchFilters, SearchRequest, SubgraphRequest

pytestmark = pytest.mark.storage


def topology_batch(chunk, profile):
    names = {}
    edges = []
    for line in chunk.text.splitlines():
        if " uses " not in line:
            continue
        left, right = line.split(" uses ")
        for value in (left, right):
            name, alias, description = value.split("|")
            names[name] = ExtractedEntity(
                local_id=name,
                name=name,
                entity_type="concept",
                description=description,
                aliases=(alias,),
                identifier=f"urn:fixture:{name.lower()}",
                spans=chunk.spans,
            )
        edges.append(
            ExtractedAssertion(
                subject=left.split("|")[0],
                target=right.split("|")[0],
                predicate="uses",
                confidence=0.9,
                spans=chunk.spans,
            )
        )
    schema = schema_for(Namespace(org_id=chunk.org_id, kg_id=chunk.kg_id), "general")
    return ExtractionBatch(
        **{key: getattr(chunk, key) for key in ("org_id", "kg_id", "material_id", "version_id")},
        chunk_id=chunk.id,
        schema_id=schema.id,
        schema_revision=schema.revision,
        provider_profile_id=profile,
        entities=tuple(names.values()),
        assertions=tuple(edges),
    )


def node(name, *, hidden=False):
    alias = "HIDDEN_ALIAS" if hidden else name + "_alias"
    description = "HIDDEN_DESCRIPTION" if hidden else name
    return f"{name}|{alias}|{description} urn:fixture:{name.lower()}"


@pytest.fixture
async def topology(material_service, team):
    publishing = await make_publishing(
        material_service,
        team,
        extraction_factory=topology_batch,
        restricted=True,
    )
    _, access, queue, coordinator, _, _ = publishing
    sources = {}
    for key, pairs, restricted, tags in (
        ("cycle", [("Alpha", "Beta"), ("Beta", "Gamma"), ("Gamma", "Alpha")], False, ("cycle",)),
        ("fan", [("Alpha", f"Leaf{i}") for i in range(12)], False, ("fan",)),
        ("hidden", [("Alpha", "Secret"), ("Secret", "Beyond")], True, ("private",)),
        ("island", [("Beyond", "Far")], False, ("island",)),
    ):
        body = "\n".join(f"{node(a, hidden=restricted)} uses {node(b)}" for a, b in pairs)
        receipt, job, checkpoint = await prepare(
            publishing,
            command=AddMaterial(
                kg_id=access.kg_ids[0],
                title=key,
                inline_text=body,
                tags=tags,
                acl_mode="restricted" if restricted else "kg",
                idempotency_key=key,
            ),
        )
        await coordinator.publish(access, await ready(publishing, job, checkpoint))
        sources[key] = receipt
    # A complete but unaccepted attempt must not seed, annotate or bridge a read.
    _, job, checkpoint = await prepare(
        publishing,
        command=AddMaterial(
            kg_id=access.kg_ids[0],
            title="pending",
            inline_text=f"{node('Alpha')} uses {node('Pending')}",
            idempotency_key="pending",
        ),
    )
    await ready(publishing, job, checkpoint)
    reader = await team[1].resolve_access(team[2]["reader"], [job.kg_id], Action.READ)
    ids = {}
    async for raw in queue.database.observations.find(
        {
            "org_id": str(job.org_id),
            "kg_id": str(job.kg_id),
        }
    ):
        ids[raw["name"]] = UUID(raw["entity"]["id"])
    return publishing, reader, ids, sources


async def test_bounded_cycle_fan_zero_hops_and_every_step_filters(topology):
    publishing, reader, ids, _ = topology
    service = GraphService(publishing[2])
    request = SubgraphRequest(kg_id=reader.kg_ids[0], seed_ids=(ids["Alpha"],))
    graph = await service.subgraph(reader, request)
    assert {node.id for node in graph.nodes} == {
        value for name, value in ids.items() if name not in ("Secret", "Beyond", "Far", "Pending")
    }
    assert len(graph.edges) == 15 and len({edge.support_id for edge in graph.edges}) == 15
    capped = await service.subgraph(
        reader, request.model_copy(update={"max_nodes": 4, "max_edges": 3})
    )
    assert len(capped.nodes) <= 4 and len(capped.edges) <= 3 and capped.truncation
    assert capped == await service.subgraph(
        reader, request.model_copy(update={"max_nodes": 4, "max_edges": 3})
    )
    zero = await service.subgraph(reader, request.model_copy(update={"max_hops": 0}))
    assert [node.id for node in zero.nodes] == [ids["Alpha"]] and zero.edges == ()
    cycle = await service.subgraph(
        reader, request.model_copy(update={"filters": SearchFilters(tags_all=("cycle",))})
    )
    assert {node.id for node in cycle.nodes} == {ids[name] for name in ("Alpha", "Beta", "Gamma")}
    assert len(cycle.edges) == 3
    empty = await service.subgraph(
        reader, request.model_copy(update={"filters": SearchFilters(min_confidence=0.95)})
    )
    assert not empty.nodes and not empty.edges
    excluded = await service.subgraph(
        reader,
        request.model_copy(update={"filters": SearchFilters(exclude_entity_ids=(ids["Beta"],))}),
    )
    assert all(ids["Beta"] not in (edge.subject_id, edge.target_id) for edge in excluded.edges)
    with pytest.raises(ValidationError):
        SubgraphRequest(kg_id=reader.kg_ids[0], seed_ids=tuple(ids.values()), max_nodes=1)
    operators = await service.repository.explain_adjacency(
        await service.resolver.resolve(
            reader, SearchRequest(kg_ids=reader.kg_ids, query="Alpha"), reader.kg_ids[0]
        ),
        ids["Alpha"],
    )
    assert any("IndexSeek" in operator for operator in operators)


async def test_visibility_change_during_expansion_fails_closed(topology, team, monkeypatch):
    from domain_kg.errors import DomainError

    publishing, reader, ids, sources = topology
    service = GraphService(publishing[2])
    original = service.repository.adjacent
    owner = await team[1].resolve_access(
        team[2]["owner"], list(reader.kg_ids), Action.MANAGE_GRANTS
    )

    async def revoke(*args, **kwargs):
        rows = await original(*args, **kwargs)
        await team[1].set_material_acl(
            owner,
            reader.kg_ids[0],
            sources["cycle"].material_id,
            "restricted",
            member_ids=(),
            group_ids=(),
            idempotency_key="during-expansion",
        )
        return rows

    monkeypatch.setattr(service.repository, "adjacent", revoke)
    with pytest.raises(DomainError) as error:
        await service.subgraph(
            reader,
            SubgraphRequest(
                kg_id=reader.kg_ids[0],
                seed_ids=(ids["Alpha"],),
                max_hops=1,
            ),
        )
    assert error.value.code == "POLICY_CHANGED"


async def test_graph_branch_uses_visible_seed_and_source_paths(topology):
    from domain_kg.retrieval.graph import GraphRetrievalBranch

    publishing, reader, ids, _ = topology
    branch = GraphRetrievalBranch(publishing[2])
    request = SearchRequest(
        kg_ids=reader.kg_ids,
        query="Alpha",
        mode="graph",
        filters=SearchFilters(tags_all=("cycle",)),
    )
    scope = await branch.resolver.resolve(reader, request, reader.kg_ids[0])
    candidates = await branch.retrieve(scope, request)
    assert candidates and all(item.graph_paths and item.chunk_id for item in candidates)
    assert all(step.support_id for item in candidates for path in item.graph_paths for step in path)
    assert all(ids["Secret"] not in item.entity_ids for item in candidates)
    hidden = request.model_copy(update={"query": "HIDDEN_ALIAS", "filters": SearchFilters()})
    scope = await branch.resolver.resolve(reader, hidden, reader.kg_ids[0])
    assert await branch.retrieve(scope, hidden) == []
