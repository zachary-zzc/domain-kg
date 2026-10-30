import json

import httpx
import pytest

from eval import scale_transport as scale


async def test_success_is_reused_and_changed_payload_fails_before_http(tmp_path):
    calls = []

    def response(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            },
        )

    ledger = scale.Runner(tmp_path, "secret", interval=0)
    payload = {"model": "qwen3.5-397b-a17b", "max_tokens": 100, "messages": []}
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        first = await ledger.call(client, "same", payload)
        assert await ledger.call(client, "same", payload) == first
        with pytest.raises(ValueError, match="changed"):
            await ledger.call(client, "same", {**payload, "max_tokens": 101})
    assert len(calls) == 1
    assert first["actual_rmb"] > 0
    assert "secret" not in json.dumps(first)


async def test_ambiguous_launch_never_automatically_repeats(tmp_path):
    runner = scale.Runner(tmp_path, "secret", interval=0)
    payload = {"model": "qwen3.5-397b-a17b", "max_tokens": 100, "messages": []}
    runner.reserve("lost:0", payload)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail())) as client:
        row = await runner.call(client, "lost", payload)
    assert row["error"] == "ambiguous_launch"


def test_unknown_price_is_not_reported_as_zero():
    assert (
        scale.cost("deepseek-r1-distill-qwen-1.5b", {"prompt_tokens": 100, "completion_tokens": 10})
        is None
    )
