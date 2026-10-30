import json

import httpx
import pytest

from eval.cloud_calls import CloudCalls, checked_endpoint, parse_answer


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://dashscope.aliyuncs.com/compatible-mode/v1",
        "https://attacker.test/compatible-mode/v1",
        "https://dashscope.aliyuncs.com@attacker.test/compatible-mode/v1",
        "https://dashscope.aliyuncs.com/compatible-mode/v1?key=x",
    ],
)
def test_credentials_cannot_be_sent_to_an_unapproved_destination(endpoint):
    with pytest.raises(ValueError):
        checked_endpoint(endpoint)


def raw(content, **choice):
    return {
        "choices": [{"finish_reason": "stop", "message": {"content": content}, **choice}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8},
    }


def test_foreign_citations_and_extra_fields_are_rejected():
    for answer in (
        {"answer": "x", "citations": ["foreign"]},
        {"answer": "x", "citations": [], "action": "execute"},
    ):
        with pytest.raises(ValueError):
            parse_answer(raw(json.dumps(answer)), {"s1"})
    assert parse_answer(raw('{"answer":"x","citations":["s1"]}'), {"s1"}) == {
        "answer": "x",
        "citations": ["s1"],
    }


def test_truncated_or_tool_bearing_responses_are_rejected():
    with pytest.raises(ValueError):
        parse_answer(raw('{"answer":"x","citations":[]}', finish_reason="length"), {"s1"})
    data = raw('{"answer":"x","citations":[]}')
    data["choices"][0]["message"]["tool_calls"] = [{"id": "tool"}]
    with pytest.raises(ValueError):
        parse_answer(data, {"s1"})


async def test_access_failure_is_saved_without_credentials_and_prevents_next_call(tmp_path):
    key = "synthetic-test-secret"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(403, json={"error": {"message": key}})
    )
    async with httpx.AsyncClient(transport=transport) as client:
        calls = CloudCalls(
            "https://dashscope.aliyuncs.com/compatible-mode/v1", key, tmp_path, client=client
        )
        result = await calls.invoke("case/model", {"model": "test"}, {"s1"})
        assert result["error"] == "http_403"
        assert key not in (tmp_path / "call-001.json").read_text()
        with pytest.raises(RuntimeError, match="halted"):
            await calls.invoke("second/model", {"model": "test"}, {"s1"})
        assert len(list(tmp_path.glob("call-*.json"))) == 1
