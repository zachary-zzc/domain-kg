import json

import httpx
import pytest

from eval import kg_quality_api as api
from eval import kg_quality_bbio as bbio


async def test_remote_route_pins_weights_keeps_budgets_and_never_sends_cloud_key(tmp_path):
    attempts = []
    model = next(iter(bbio.MODELS))
    spec = bbio.MODELS[model]

    def respond(request):
        attempts.append(request)
        assert "cloud-secret" not in str(request.headers)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": spec["tag"],
                            "digest": spec["digest"],
                            "details": {
                                "parameter_size": spec["parameter_size"],
                                "quantization_level": "Q4_K_M",
                            },
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "model": spec["tag"],
                "done": True,
                "done_reason": "stop",
                "message": {"role": "assistant", "content": '{"ok":true}'},
                "prompt_eval_count": 14,
                "eval_count": 5,
            },
        )

    calls = api.CloudCalls(tmp_path, "cloud-secret")
    payload = api.chat_request(model, "Return JSON.", {"source": "fixture"}, 2048)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        first = await calls.chat(client, payload)
        assert await calls.chat(client, payload) == first
    assert first["content"] == '{"ok":true}'
    posts = [r for r in attempts if r.method == "POST"]
    assert len(posts) == 1
    wire = json.loads(posts[0].content)
    assert str(posts[0].url) == bbio.BASE + "/api/chat"
    assert wire["messages"] == payload["messages"]
    assert wire["options"]["num_ctx"] == 16384
    assert wire["options"]["num_predict"] == 2048
    assert wire["format"] == "json"
    saved = next(tmp_path.glob("*.result.json")).read_text()
    assert "cloud-secret" not in saved and "native_response" in saved


async def test_remote_digest_drift_blocks_inference(tmp_path):
    def changed(request):
        assert request.method == "GET"
        spec = next(iter(bbio.MODELS.values()))
        return httpx.Response(200, json={"models": [{"name": spec["tag"], "digest": "changed"}]})

    payload = api.chat_request(next(iter(bbio.MODELS)), "Return JSON.", {}, 64)
    async with httpx.AsyncClient(transport=httpx.MockTransport(changed)) as client:
        with pytest.raises(ValueError, match="identity"):
            await api.CloudCalls(tmp_path, "cloud-secret").chat(client, payload)
    assert not list(tmp_path.glob("*.launch.json"))


@pytest.mark.parametrize(
    "changes",
    [
        {"done": False},
        {"done_reason": "length"},
        {"model": "different-model"},
        {"message": {"role": "assistant", "content": "", "tool_calls": [{}]}},
    ],
)
def test_native_completion_errors_remain_errors(changes):
    spec = next(iter(bbio.MODELS.values()))
    raw = {
        "model": spec["tag"],
        "done": True,
        "done_reason": "stop",
        "message": {"role": "assistant", "content": '{"ok":true}'},
        "prompt_eval_count": 10,
        "eval_count": 5,
    }
    with pytest.raises(ValueError):
        bbio.completion({**raw, **changes}, spec["tag"])


async def test_remote_identity_failure_cannot_publish_valid_looking_extraction():
    from eval.kg_quality_build import ExtractionWire

    class Calls:
        async def raw(self, client, payload):
            return {
                "http_status": 200,
                "error": "remote_identity_unverified_after_call",
                "response": {"choices": []},
            }

    model = next(iter(bbio.MODELS))
    request = httpx.Request("POST", bbio.BASE + "/v1/chat/completions", json={"model": model})
    with pytest.raises(httpx.TransportError):
        await ExtractionWire(Calls(), None, model).handle_async_request(request)


def test_remote_profile_increases_only_deadline_not_evidence_or_output_budget():
    from uuid import uuid4

    from pydantic import ValidationError

    from domain_kg.models import Namespace
    from domain_kg.providers.profiles import ProviderProfile
    from eval.kg_quality_build import profile_for

    ns = Namespace(org_id=uuid4(), kg_id=uuid4())
    remote, cloud = (profile_for(m, ns) for m in (api.WORK_MODELS[0], api.WORK_MODELS[2]))
    assert remote.timeout_seconds == 600 and cloud.timeout_seconds == 120
    assert remote.extraction_context_tokens == cloud.extraction_context_tokens == 16384
    assert remote.max_completion_tokens == cloud.max_completion_tokens == 8192
    with pytest.raises(ValidationError):
        ProviderProfile.model_validate({**remote.model_dump(), "timeout_seconds": 601})
