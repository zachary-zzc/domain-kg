"""Real accepted stores plus deliberate branch and current-policy failures."""
# ruff: noqa: F811

import asyncio
from uuid import uuid4

import pytest
from test_retrieval_corpus import retrieval_corpus  # noqa: F401

from domain_kg.errors import BranchUnavailable, DomainError, ErrorCode
from domain_kg.retrieval.models import SearchFilters, SearchRequest
from domain_kg.retrieval.service import RetrievalService

pytestmark = pytest.mark.storage


async def test_fast_fusion_empty_scope_and_partial_branch_rules(retrieval_corpus, monkeypatch):
    publishing, reader, receipts = retrieval_corpus
    service = RetrievalService(publishing[2], publishing[3].providers)
    request = SearchRequest(kg_ids=reader.kg_ids, query="Alpha")

    async def forbidden(*args, **kwargs):
        pytest.fail("fast must not invoke graph or generative query operations")

    monkeypatch.setattr(service.deep, "retrieve", forbidden)
    monkeypatch.setattr(service.queries, "keywords", forbidden)
    monkeypatch.setattr(service.queries, "rerank", forbidden)
    result = await service.search(reader, request)
    assert result.status == "ok" and result.results
    assert all(item.material_id != receipts["hidden"].material_id for item in result.results)
    assert {item.branch for item in result.branch_status if item.status == "skipped"} == {
        "graph",
        "rerank",
    }
    assert set(result.results[0].method_ranks) == {"vector", "lexical"}
    empty = await service.search(
        reader, request.model_copy(update={"filters": SearchFilters(tags_all=("does-not-exist",))})
    )
    assert empty.status == "ok" and empty.results == ()

    async def unavailable(*args, **kwargs):
        raise BranchUnavailable("Vector recall is unavailable.")

    monkeypatch.setattr(service.vector, "retrieve", unavailable)
    with pytest.raises(DomainError, match="unavailable"):
        await service.search(reader, request)
    partial_request = request.model_copy(update={"allow_partial": True})
    partial = await service.search(reader, partial_request)
    assert partial.status == "partial" and partial.results
    assert next(s for s in partial.branch_status if s.branch == "vector").status == "unavailable"

    async def unsupported(*args, **kwargs):
        raise DomainError(ErrorCode.UNSUPPORTED_FILTER, "The branch cannot enforce this filter.")

    monkeypatch.setattr(service.vector, "retrieve", unsupported)
    partial = await service.search(reader, partial_request)
    assert partial.status == "partial"
    assert next(s for s in partial.branch_status if s.branch == "vector").status == "unsupported"
    monkeypatch.setattr(service.vector, "retrieve", unavailable)
    monkeypatch.setattr(service.lexical, "retrieve", unavailable)
    with pytest.raises(DomainError):
        await service.search(reader, partial_request)


async def test_authority_integrity_and_forged_candidates_never_become_partial(
    retrieval_corpus, monkeypatch
):
    publishing, reader, _ = retrieval_corpus
    service = RetrievalService(publishing[2], publishing[3].providers)
    request = SearchRequest(kg_ids=reader.kg_ids, query="Alpha", allow_partial=True)
    original = service.vector.retrieve
    for code in (
        ErrorCode.BACKEND_UNAVAILABLE,
        ErrorCode.PROJECTION_INCONSISTENT,
        ErrorCode.POLICY_CHANGED,
    ):

        async def fatal(*args, code=code, **kwargs):
            raise DomainError(code, "Authority or integrity failed.")

        monkeypatch.setattr(service.vector, "retrieve", fatal)
        with pytest.raises(DomainError) as error:
            await service.search(reader, request)
        assert error.value.code == code

    async def forged(scope, request):
        items = await original(scope, request)
        return [items[0].model_copy(update={"kg_id": uuid4()})]

    monkeypatch.setattr(service.vector, "retrieve", forged)
    with pytest.raises(DomainError) as error:
        await service.search(reader, request)
    assert error.value.code == ErrorCode.PROJECTION_INCONSISTENT

    async def altered(scope, request):
        items = await original(scope, request)
        return [items[0].model_copy(update={"text": "fabricated evidence"})]

    monkeypatch.setattr(service.vector, "retrieve", altered)
    with pytest.raises(DomainError) as error:
        await service.search(reader, request)
    assert error.value.code == ErrorCode.PROJECTION_INCONSISTENT


async def test_scope_change_after_recall_and_concurrent_filters(retrieval_corpus, monkeypatch):
    publishing, reader, receipts = retrieval_corpus
    service = RetrievalService(publishing[2], publishing[3].providers)
    kg = reader.kg_ids[0]
    public, plain = await asyncio.gather(
        *[
            service.search(
                reader,
                SearchRequest(
                    kg_ids=(kg,),
                    query="Alpha",
                    mode="vector",
                    filters=SearchFilters(material_ids=(receipts[key].material_id,)),
                ),
            )
            for key in ("public", "plain")
        ]
    )
    assert {item.material_id for item in public.results} == {receipts["public"].material_id}
    assert {item.material_id for item in plain.results} == {receipts["plain"].material_id}
    original = service.vector.retrieve

    async def changed(scope, request):
        items = await original(scope, request)
        await publishing[2].database.kgs.update_one(
            {"org_id": str(reader.org_id), "id": str(kg)}, {"$inc": {"visibility_revision": 1}}
        )
        return items

    monkeypatch.setattr(service.vector, "retrieve", changed)
    with pytest.raises(DomainError) as error:
        await service.search(
            reader, SearchRequest(kg_ids=(kg,), query="Alpha", mode="vector", allow_partial=True)
        )
    assert error.value.code == ErrorCode.POLICY_CHANGED


async def test_deep_calls_pinned_native_mix_and_bounds_visible_graph(retrieval_corpus, monkeypatch):
    from functools import wraps

    import lightrag.operate

    publishing, reader, receipts = retrieval_corpus
    service = RetrievalService(publishing[2], publishing[3].providers)
    native = lightrag.operate._perform_kg_search
    calls = []

    @wraps(native)
    async def observed(*args, **kwargs):
        calls.append(kwargs["query_param"].mode)
        return await native(*args, **kwargs)

    monkeypatch.setattr(lightrag.operate, "_perform_kg_search", observed)
    result = await service.search(
        reader,
        SearchRequest(kg_ids=reader.kg_ids, query="Alpha", mode="deep", max_nodes=2, max_edges=1),
    )
    assert calls == ["mix"] and result.results
    paths = [path for item in result.results for path in item.graph_paths]
    assert paths and all(len(path) <= 2 for path in paths)
    assert all(item.material_id != receipts["hidden"].material_id for item in result.results)
    assert "HIDDEN" not in result.model_dump_json()


async def test_opt_in_keywords_and_reranking_only_select_existing_visible_data(retrieval_corpus):
    import json

    import httpx

    publishing, reader, _ = retrieval_corpus
    providers = publishing[3].providers
    original = providers.transport
    calls, invalid = [], []

    async def respond(request):
        body = json.loads(request.content)
        name = body.get("response_format", {}).get("json_schema", {}).get("name")
        if name not in ("Keywords", "Ranking"):
            return await original.handle_async_request(request)
        calls.append(name)
        payload = json.loads(body["messages"][1]["content"])
        assert "PRIVATE_NEAREST" not in json.dumps(body)
        value = (
            {"keywords": ["Alpha"]}
            if name == "Keywords"
            else {"order": [999] if invalid else list(reversed(range(len(payload["evidence"]))))}
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    providers.transport = httpx.MockTransport(respond)
    service = RetrievalService(publishing[2], providers)
    request = SearchRequest(
        kg_ids=reader.kg_ids, query="Alpha", mode="deep", generate_keywords=True, rerank=True
    )
    result = await service.search(reader, request)
    assert calls == ["Keywords", "Ranking"]
    assert any(warning.startswith("keyword_generation_ms:") for warning in result.warnings)
    assert all("rerank" in item.method_ranks for item in result.results)
    invalid.append(True)
    with pytest.raises(DomainError) as error:
        await service.search(reader, request)
    assert error.value.code == ErrorCode.PROVIDER_UNAVAILABLE


async def test_two_kgs_different_spaces_later_best_and_authorization_before_fanout(
    retrieval_corpus, catalog, team, monkeypatch
):
    from test_publication import make_publishing, prepare, ready
    from test_retrieval_corpus import corpus_embedding, corpus_extraction

    from domain_kg.catalog.models import CreateKG
    from domain_kg.identity.models import Action
    from domain_kg.materials.models import AddMaterial
    from domain_kg.providers.profiles import ProviderConfig, ProviderRegistry

    publishing, reader, _ = retrieval_corpus
    materials, _, queue, coordinator, _, _ = publishing
    creator = await team[1].resolve_access(team[2]["owner"], [], Action.CREATE_KG)
    second = await catalog[0].create_kg(
        creator,
        CreateKG(
            name="Later KG",
            domain_profile_id=catalog[0].general_profile_id,
            placement_id=catalog[3].id,
            idempotency_key="later-kg",
        ),
    )
    grant = await team[1].resolve_access(team[2]["owner"], [second.kg_id], Action.MANAGE_GRANTS)
    for name in ("editor", "reader"):
        await team[1].set_grant(
            grant, second.kg_id, "member", team[3][name].id, name, idempotency_key="later-" + name
        )
        grant = await team[1].resolve_access(team[2]["owner"], [second.kg_id], Action.MANAGE_GRANTS)
    other = await make_publishing(
        (materials, second.kg_id),
        team,
        extraction_factory=corpus_extraction,
        embedding_factory=corpus_embedding,
        embedding_model="different-embedding-space",
    )
    providers2 = other[3].providers
    profile2 = next(iter(providers2.registry.profiles.values()))
    receipt, job, checkpoint = await prepare(
        other,
        command=AddMaterial(
            kg_id=second.kg_id, title="exact", inline_text="Alpha", idempotency_key="later-evidence"
        ),
    )
    await other[3].publish(other[1], await ready(other, job, checkpoint))
    providers = coordinator.providers
    original = providers.transport
    registry1 = providers.registry
    providers.registry = ProviderRegistry(
        ProviderConfig(
            profiles=(*registry1.profiles.values(), profile2),
            connections=tuple(registry1.connections.values()),
        )
    )
    # The mock uses one approved endpoint but returns each selected model's dimensions/name.
    import json

    import httpx

    async def route(request):
        if json.loads(request.content).get("model") == profile2.embedding_model:
            return httpx.Response(
                200,
                json={
                    "model": profile2.embedding_model,
                    "data": [{"index": 0, "embedding": corpus_embedding("Alpha")}],
                    "usage": {"prompt_tokens": 1},
                },
            )
        return await original.handle_async_request(request)

    providers.transport = httpx.MockTransport(route)
    access = await team[1].resolve_access(
        team[2]["reader"], [reader.kg_ids[0], second.kg_id], Action.SEARCH
    )
    service = RetrievalService(queue, providers)
    result = await service.search(
        access, SearchRequest(kg_ids=access.kg_ids, query="Alpha", top_k=2)
    )
    assert {item.kg_id for item in result.results} == set(access.kg_ids)
    assert any(item.material_id == receipt.material_id for item in result.results)
    selected1 = await providers.selected(access, reader.kg_ids[0])
    selected2 = await providers.selected(access, second.kg_id)
    assert selected1["embedding_space_id"] != selected2["embedding_space_id"]
    calls = []

    async def observed(*args, **kwargs):
        calls.append(True)
        return []

    monkeypatch.setattr(service.vector, "retrieve", observed)
    with pytest.raises(DomainError):
        await service.search(
            access,
            SearchRequest(kg_ids=(*access.kg_ids, uuid4()), query="Alpha", allow_partial=True),
        )
    assert calls == []
