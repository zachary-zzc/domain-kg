import json

import httpx
import pytest

from eval import kg_quality_api as api


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:11434/v1",
        "https://127.0.0.1/v1",
        "http://dashscope.aliyuncs.com/v1",
        "https://dashscope.aliyuncs.com.attacker.test/compatible-mode/v1",
    ],
)
def test_cloud_only_rejects_local_proxy_and_unapproved_endpoint(endpoint):
    with pytest.raises(ValueError, match="API-only"):
        api.validate_endpoint(endpoint)


async def test_calls_pin_model_and_endpoint_and_replay_without_second_request(tmp_path):
    attempts = []

    def transport(request):
        attempts.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    calls = api.CloudCalls(tmp_path, "fixture-key")
    payload = api.chat_request(api.WORK_MODELS[2], "Return JSON.", {"input": "fixture"}, 64)
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        first = await calls.chat(client, payload)
        second = await calls.chat(client, payload)
    assert first == second and first["content"] == '{"ok":true}'
    assert len(attempts) == 1 and attempts[0]["model"] == api.WORK_MODELS[2]
    assert "fixture-key" not in "".join(p.read_text() for p in tmp_path.glob("*.json"))


async def test_unapproved_work_model_cannot_be_silently_substituted(tmp_path):
    calls = api.CloudCalls(tmp_path, "fixture-key")
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError, match="model"):
            await calls.chat(client, {"model": "local-model", "max_tokens": 64})
    assert not list(tmp_path.glob("*.json"))


def test_every_build_profile_uses_its_row_model_and_shared_embedding():
    from uuid import NAMESPACE_URL, uuid5

    from domain_kg.models import Namespace
    from eval.kg_quality_build import profile_for

    ns = Namespace(org_id=uuid5(NAMESPACE_URL, "org"), kg_id=uuid5(NAMESPACE_URL, "kg"))
    profiles = [profile_for(m, ns) for m in api.WORK_MODELS]
    assert [p.extraction_model for p in profiles] == list(api.WORK_MODELS)
    assert len({p.id for p in profiles}) == 6
    assert {p.embedding_model for p in profiles} == {"text-embedding-v4"}


def test_construction_prompt_policy_changes_only_semantics():
    from uuid import uuid4

    from domain_kg.models import Namespace
    from eval.kg_quality_build import profile_for

    ns = Namespace(org_id=uuid4(), kg_id=uuid4())
    before = profile_for(api.WORK_MODELS[2], ns)
    after = profile_for(api.WORK_MODELS[2], ns, semantics="source_facets_v1")
    assert before.model_dump(exclude={"extraction_semantics"}) == after.model_dump(
        exclude={"extraction_semantics"}
    )
    with pytest.raises(ValueError, match="semantics"):
        profile_for(api.WORK_MODELS[2], ns, semantics="unplanned")


async def test_ambiguous_launch_never_resubmits_or_runs_fallback(tmp_path):
    from eval.source_role_eval import digest

    payload = api.chat_request(api.WORK_MODELS[2], "Return JSON", {}, 64)
    url = api.API_BASE + "/chat/completions"
    identity = digest([url, payload])
    api.save_once(
        tmp_path / (identity + ".launch.json"),
        {"endpoint": url, "payload": payload, "request_sha256": digest(payload)},
    )

    def unexpected(_):
        pytest.fail("An ambiguous launch must not send another API request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        row = await api.CloudCalls(tmp_path, "key").chat(client, payload)
    assert row["error"] == "ambiguous_launch"


async def test_redirects_and_authorization_failure_do_not_call_another_endpoint(tmp_path):
    attempts = []

    def denied(request):
        attempts.append(str(request.url))
        return httpx.Response(403, json={"error": {"code": "AccessDenied"}})

    calls = api.CloudCalls(tmp_path, "key")
    payload = api.chat_request(api.WORK_MODELS[2], "Return JSON", {}, 64)
    async with httpx.AsyncClient(transport=httpx.MockTransport(denied)) as client:
        for _ in range(2):
            with pytest.raises(RuntimeError, match="authorization"):
                await calls.chat(client, payload)
    assert attempts == [api.API_BASE + "/chat/completions"]
    async with httpx.AsyncClient(follow_redirects=True) as client:
        with pytest.raises(ValueError, match="redirect"):
            await calls.chat(client, payload)


async def test_production_construction_binds_and_replays_same_model(tmp_path, monkeypatch):
    from test_extraction_validation import source

    from eval.kg_quality_build import construct
    from eval.source_role_run import read

    chunk = source()
    text = chunk.text
    api.save_once(
        tmp_path / "corpus.json",
        [
            {
                "id": "doc",
                "dataset": "fixture",
                "title": "Fixture",
                "text": text,
                "units": [{"id": "u", "text": text}],
            }
        ],
    )
    api.save_once(
        tmp_path / "windows.json", [{"document": "doc", "chunk": chunk.model_dump(mode="json")}]
    )
    api.save_once(tmp_path / "protocol.json", {"construction_semantics": "source_facets_v2"})
    attempts = []

    async def bounded(_client, url, payload, _key, **_kwargs):
        attempts.append((url, payload["model"]))
        assert "Policy source_facets_v2" in payload["messages"][0]["content"]
        return {
            "error": None,
            "http_status": 200,
            "seconds": 0.01,
            "response": {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"entities":[],"assertions":[]}'},
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        }

    monkeypatch.setattr(api, "bounded_call", bounded)
    for model in api.WORK_MODELS[2:4]:
        await construct(tmp_path, model, "key")
        await construct(tmp_path, model, "key")
        graph = read(tmp_path / "construction" / model / "graph.json")
        assert graph["model"] == model
        assert graph["facts"] == []
    assert attempts == [(api.API_BASE + "/chat/completions", m) for m in api.WORK_MODELS[2:4]]


async def test_cloud_client_does_not_keep_httpx_five_second_read_timeout():
    async with api.cloud_client() as client:
        assert client.timeout.read == 180
        assert not client.follow_redirects


async def test_connection_retry_preserves_attempts_and_never_rejudges_success(tmp_path):
    count = 0

    def transport(request):
        nonlocal count
        count += 1
        if count == 1:
            raise httpx.ConnectError("temporary", request=request)
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": '{"correct":false}'}}]
            },
        )

    calls = api.CloudCalls(tmp_path, "key")
    payload = api.chat_request(api.WORK_MODELS[2], "Judge once", {}, 64)
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        first = await calls.chat(client, payload)
        second = await calls.chat(client, payload)
    assert first == second and first["content"] == '{"correct":false}'
    assert count == 2
    assert len(list(tmp_path.rglob("*.result.json"))) == 2


async def test_transport_retry_is_bounded(tmp_path):
    count = 0

    def transport(request):
        nonlocal count
        count += 1
        raise httpx.ConnectError("temporary", request=request)

    payload = api.chat_request(api.WORK_MODELS[2], "Judge once", {}, 64)
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        result = await api.CloudCalls(tmp_path, "key").chat(client, payload)
    assert count == 4 and result["error"] == "ConnectError"
