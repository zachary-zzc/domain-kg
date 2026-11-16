import pytest
from test_fusion import evidence

from domain_kg.errors import DomainError
from domain_kg.providers.rerank import Ranking, ranked_sources, ranking_payload


def test_source_ranking_reads_complete_originals_without_graph_hints_or_branch_bias():
    item = evidence(text="A long introduction. " * 500 + "The needed relationship is at the end.")
    payload = ranking_payload("What relationship is stated?", [item])
    assert payload == {
        "query": "What relationship is stated?",
        "evidence": [{"index": 0, "text": item.text}],
    }


def test_model_ranking_only_reorders_original_candidates():
    first, second = evidence(), evidence()
    assert ranked_sources([first, second], Ranking(order=(1, 0))) == [second, first]
    assert ranked_sources([first, second], Ranking(order=(1,))) == [second]
    assert ranked_sources([first, second], Ranking(order=())) == []
    for order in ((0, 0), (0, 2)):
        with pytest.raises(DomainError):
            ranked_sources([first, second], Ranking(order=order))


def test_source_ranking_never_silently_drops_candidates_over_its_cap():
    with pytest.raises(ValueError):
        ranking_payload("q", [evidence() for _ in range(51)])


@pytest.mark.parametrize("response_format", ["json_schema", "json_object"])
async def test_query_provider_sends_full_sources_and_rechecks_authority(response_format):
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    import httpx
    from test_compact_extraction import compact_profile
    from test_extraction_validation import source

    from domain_kg.providers.rerank import QueryProvider
    from domain_kg.retrieval.models import SearchRequest

    chunk = source()
    profile, connection = compact_profile(chunk)
    profile = profile.model_copy(
        update={
            "extraction_context_tokens": 32768,
            "extraction_response_format": response_format,
            "extraction_enable_thinking": False,
        }
    )
    item = evidence(kg=chunk.kg_id, text="Original source. " * 1000 + "Necessary fact at end.")
    scope = SimpleNamespace(
        access=object(), view=SimpleNamespace(org_id=chunk.org_id, kg_id=chunk.kg_id)
    )
    bodies = []

    def respond(request):
        body = json.loads(request.content)
        bodies.append(body)
        assert json.loads(body["messages"][1]["content"])["evidence"][0]["text"] == item.text
        assert body["response_format"]["type"] == response_format
        assert body["enable_thinking"] is False
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": '{"order":[0]}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
        )

    providers = SimpleNamespace(
        selected=AsyncMock(
            return_value={"profile_id": str(profile.id), "profile_revision": profile.revision}
        ),
        policy=SimpleNamespace(material_visible=AsyncMock(return_value=True)),
        database=SimpleNamespace(
            material_access=SimpleNamespace(
                find_one=AsyncMock(return_value={"acl_mode": "public"})
            ),
            provider_usage=SimpleNamespace(update_one=AsyncMock()),
        ),
        registry=SimpleNamespace(resolve=Mock(return_value=(profile, connection))),
        transport=httpx.MockTransport(respond),
    )
    resolver = SimpleNamespace(revalidate=AsyncMock())
    query = QueryProvider(providers, resolver)
    request = SearchRequest(kg_ids=(chunk.kg_id,), query="Which fact?", mode="deep", rerank=True)
    assert await query.rerank(scope, request, [item]) == [item]
    assert resolver.revalidate.await_count == 2 and len(bodies) == 1
    small = profile.model_copy(update={"extraction_context_tokens": 4096})
    providers.registry.resolve.return_value = (small, connection)
    with pytest.raises(DomainError, match="context window"):
        await query.rerank(scope, request, [item])
    assert len(bodies) == 1
