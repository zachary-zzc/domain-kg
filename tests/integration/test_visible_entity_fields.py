"""Names, aliases, descriptions and counts have the same evidence visibility."""

import pytest
from test_graph_retrieval import topology  # noqa: F401

from domain_kg.errors import DomainError
from domain_kg.retrieval.models import SearchFilters, SearchRequest, SubgraphRequest

pytestmark = pytest.mark.storage


async def test_visible_fields_counts_and_no_hidden_bridge(topology, monkeypatch):  # noqa: F811
    from domain_kg.engine.graph import ScopedLightRAGGraph
    from domain_kg.graph.service import GraphService

    publishing, reader, ids, sources = topology
    service = GraphService(publishing[2])

    # A graph-only read cannot accidentally open the vector backend.
    async def forbidden(*args, **kwargs):
        raise AssertionError("full/vector store routing on graph-only read")

    monkeypatch.setattr(publishing[0].router, "for_kg", forbidden)
    node = await service.get_node(reader, reader.kg_ids[0], ids["Alpha"])
    assert node.name == "Alpha" and "HIDDEN" not in node.model_dump_json()
    assert node.support_count == 14 and node.neighbor_count == 14
    assert all(
        observation.material_id != sources["hidden"].material_id
        for observation in node.observations
    )
    with pytest.raises(DomainError) as error:
        await service.get_node(reader, reader.kg_ids[0], ids["Secret"])
    assert error.value.code == "RESOURCE_NOT_ACCESSIBLE"
    graph = await service.subgraph(
        reader,
        SubgraphRequest(
            kg_id=reader.kg_ids[0],
            seed_ids=(ids["Alpha"],),
            filters=SearchFilters(tags_all=("cycle",)),
        ),
    )
    assert {node.id for node in graph.nodes} == {ids[name] for name in ("Alpha", "Beta", "Gamma")}
    request = SearchRequest(kg_ids=reader.kg_ids, query="Alpha")
    scope = await service.resolver.resolve(reader, request, reader.kg_ids[0])
    adapter = ScopedLightRAGGraph(service.repository, scope)
    value = await adapter.get_node(str(ids["Alpha"]))
    assert value and "HIDDEN" not in str(value)
    assert await adapter.get_node(str(ids["Secret"])) is None
    assert all(
        str(ids["Secret"]) not in pair for pair in await adapter.get_node_edges(str(ids["Alpha"]))
    )


async def test_concurrent_scopes_and_projection_corruption(topology, team, monkeypatch):  # noqa: F811
    import asyncio

    from domain_kg.graph.service import GraphService
    from domain_kg.identity.models import Action

    publishing, reader, ids, sources = topology
    queue = publishing[2]
    service = GraphService(queue)
    editor = await team[1].resolve_access(team[2]["editor"], list(reader.kg_ids), Action.READ)
    write_only = await team[1].resolve_access(
        team[2]["editor"].model_copy(update={"scopes": frozenset({"domain_kg:write"})}),
        list(reader.kg_ids),
        Action.INGEST,
    )
    with pytest.raises(DomainError):
        await service.get_node(write_only, reader.kg_ids[0], ids["Alpha"])
    nodes = await asyncio.gather(
        *(
            service.get_node(access, reader.kg_ids[0], ids["Alpha"])
            for access in (reader, editor, reader)
        )
    )
    assert ["HIDDEN_ALIAS" in node.aliases for node in nodes] == [False, True, False]
    assert [node.support_count for node in nodes] == [14, 15, 14]
    from pymongo.errors import AutoReconnect

    async def authority_unavailable(*args, **kwargs):
        raise AutoReconnect("synthetic authority outage")

    with monkeypatch.context() as patch:
        patch.setattr("domain_kg.graph.repository.accepted_document", authority_unavailable)
        with pytest.raises(AutoReconnect):
            await service.get_node(reader, reader.kg_ids[0], ids["Alpha"])
    version_query = {
        "org_id": str(reader.org_id),
        "kg_id": str(reader.kg_ids[0]),
        "id": str(sources["cycle"].version_id),
    }
    stores = await queue.router.for_sources(reader, reader.kg_ids[0])
    await stores.authority._database.material_versions.update_one(
        version_query,
        {
            "$set": {"material.id": str(sources["fan"].material_id)},
        },
    )
    with pytest.raises(DomainError):
        await service.get_node(reader, reader.kg_ids[0], ids["Alpha"])
    await stores.authority._database.material_versions.update_one(
        version_query,
        {
            "$set": {"material.id": str(sources["cycle"].material_id)},
        },
    )
    binding = await queue.router.for_graph(reader, reader.kg_ids[0])
    # An unreadable material is not opened or integrity-probed by this reader.
    for key in ("hidden", "cycle"):
        await binding.driver.execute_query(
            "MATCH (n:ScopedProjection {org_id:$org,kg_id:$kg,material_id:$material}) "
            "SET n.text='synthetic-corruption'",
            org=str(reader.org_id),
            kg=str(reader.kg_ids[0]),
            material=str(sources[key].material_id),
        )
        if key == "hidden":
            assert (
                await service.get_node(reader, reader.kg_ids[0], ids["Alpha"])
            ).support_count == 14
        else:
            with pytest.raises(DomainError) as error:
                await service.get_node(reader, reader.kg_ids[0], ids["Alpha"])
            assert error.value.code == "PROJECTION_INCONSISTENT"
    assert (
        await queue.database.repair_requests.count_documents(
            {
                "org_id": str(reader.org_id),
                "kg_id": str(reader.kg_ids[0]),
                "state": "pending_authorized_repair",
            }
        )
        == 1
    )
