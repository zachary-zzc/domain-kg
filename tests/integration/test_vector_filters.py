"""Qdrant must filter before top-k and authenticate every returned payload."""
# ruff: noqa: F811

from datetime import UTC, datetime, timedelta

import pytest
from test_retrieval_corpus import retrieval_corpus  # noqa: F401

from domain_kg.engine.vector import ScopedLightRAGVector
from domain_kg.retrieval.models import SearchFilters, SearchRequest
from domain_kg.retrieval.vector import VectorRetrievalBranch

pytestmark = pytest.mark.storage


async def test_hidden_nearest_neighbor_support_filters_and_current_metadata(
    retrieval_corpus,
    monkeypatch,  # noqa: F811
):
    publishing, reader, receipts = retrieval_corpus
    branch = VectorRetrievalBranch(publishing[2], publishing[3].providers)

    async def forbidden(*args, **kwargs):
        raise AssertionError("vector-only read opened a graph/full backend")

    monkeypatch.setattr(publishing[0].router, "for_graph", forbidden)
    monkeypatch.setattr(publishing[0].router, "for_kg", forbidden)
    request = SearchRequest(kg_ids=reader.kg_ids, query="needle", mode="vector", top_k=1)
    scope = await branch.resolver.resolve(reader, request, reader.kg_ids[0])
    result = await branch.retrieve(scope, request)
    assert result and result[0].material_id == receipts["public"].material_id
    assert all(item.material_id != receipts["hidden"].material_id for item in result)
    from lightrag import QueryParam
    from lightrag.operate import _get_vector_context

    adapter = ScopedLightRAGVector(branch, scope)
    context = await _get_vector_context("needle", adapter, QueryParam(mode="naive", chunk_top_k=1))
    assert len(context) == 1 and context[0]["chunk_id"] == str(result[0].chunk_id)
    exact = await branch.retrieve(scope, request, exact=True)
    assert [item.chunk_id for item in result] == [item.chunk_id for item in exact]
    from uuid import UUID

    observation = await publishing[2].database.observations.find_one(
        {
            "org_id": str(reader.org_id),
            "kg_id": str(reader.kg_ids[0]),
            "material_id": str(receipts["public"].material_id),
            "name": "Alpha",
        }
    )
    entity = UUID(observation["entity"]["id"])
    for filters, expected in (
        (SearchFilters(tags_all=("plain",)), {receipts["plain"].material_id}),
        (SearchFilters(predicates=("uses",)), {receipts["public"].material_id}),
        (SearchFilters(entity_types=("concept",)), {receipts["public"].material_id}),
        (SearchFilters(entity_ids=(entity,)), {receipts["public"].material_id}),
        (SearchFilters(entity_types=("absent-type",)), set()),
        (
            SearchFilters(tags_all=("public",), languages=("en",), authors=("fixture-author",)),
            {receipts["public"].material_id},
        ),
        (SearchFilters(min_confidence=0.95), set()),
        (SearchFilters(created_from=datetime.now(UTC) + timedelta(days=1)), set()),
        (
            SearchFilters(
                material_ids=(receipts["public"].material_id,),
                version_id=receipts["public"].version_id,
            ),
            {receipts["public"].material_id},
        ),
    ):
        filtered = request.model_copy(update={"filters": filters})
        resolved = await branch.resolver.resolve(reader, filtered, reader.kg_ids[0])
        assert {item.material_id for item in await branch.retrieve(resolved, filtered)} == expected
    selected = await publishing[2].database.kg_provider_settings.find_one(
        {"kg_id": str(reader.kg_ids[0])}
    )
    await publishing[2].database.material_access.update_one(
        {
            "org_id": str(reader.org_id),
            "kg_id": str(reader.kg_ids[0]),
            "id": str(receipts["plain"].material_id),
        },
        {"$set": {"metadata.tags": ["changed"]}},
    )
    await publishing[2].database.kgs.update_one(
        {"org_id": str(reader.org_id), "id": str(reader.kg_ids[0])},
        {"$inc": {"visibility_revision": 1}},
    )
    filtered = request.model_copy(update={"filters": SearchFilters(tags_all=("changed",))})
    resolved = await branch.resolver.resolve(reader, filtered, reader.kg_ids[0])
    previous_inputs = len(publishing[5]["embedding_inputs"])
    assert [item.material_id for item in await branch.retrieve(resolved, filtered)] == [
        receipts["plain"].material_id
    ]
    assert publishing[5]["embedding_inputs"][previous_inputs:] == ["needle"]
    assert selected == await publishing[2].database.kg_provider_settings.find_one(
        {"kg_id": str(reader.kg_ids[0])}
    )


async def test_space_mismatch_corruption_and_visibility_race_fail_closed(
    retrieval_corpus,
    monkeypatch,  # noqa: F811
):
    from domain_kg.errors import DomainError

    publishing, reader, receipts = retrieval_corpus
    queue = publishing[2]
    branch = VectorRetrievalBranch(queue, publishing[3].providers)
    request = SearchRequest(kg_ids=reader.kg_ids, query="needle", mode="vector")
    scope = await branch.resolver.resolve(reader, request, reader.kg_ids[0])
    key = {"org_id": str(reader.org_id), "kg_id": str(reader.kg_ids[0])}
    selected = await queue.database.kg_provider_settings.find_one(key)
    await queue.database.kg_provider_settings.update_one(
        key, {"$set": {"embedding_space_id": "b" * 64}}
    )
    with pytest.raises(DomainError) as error:
        await branch.retrieve(scope, request)
    assert error.value.code == "VERSION_CONFLICT"
    await queue.database.kg_provider_settings.update_one(
        key, {"$set": {"embedding_space_id": selected["embedding_space_id"]}}
    )
    binding = await queue.router.for_vectors(reader, reader.kg_ids[0])
    original = binding.client.query_points

    async def revoke(*args, **kwargs):
        result = await original(*args, **kwargs)
        await queue.database.kgs.update_one(
            {"org_id": str(reader.org_id), "id": str(reader.kg_ids[0])},
            {"$inc": {"visibility_revision": 1}},
        )
        return result

    with monkeypatch.context() as patch:
        patch.setattr(binding.client, "query_points", revoke)
        with pytest.raises(DomainError) as error:
            await branch.retrieve(scope, request)
        assert error.value.code == "POLICY_CHANGED"
    scope = await branch.resolver.resolve(reader, request, reader.kg_ids[0])
    from qdrant_client import models

    await binding.client.set_payload(
        binding.collection,
        payload={"text": "synthetic-corruption"},
        points=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="material_id",
                        match=models.MatchValue(value=str(receipts["public"].material_id)),
                    )
                ]
            )
        ),
        wait=True,
    )
    with pytest.raises(DomainError) as error:
        await branch.retrieve(scope, request)
    assert error.value.code == "PROJECTION_INCONSISTENT"
