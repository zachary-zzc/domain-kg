import json

import httpx
import pytest

from eval.source_role_eval import EXTRACTOR
from eval.source_role_run import Ledger, reserve_cost


def payload():
    return {
        "model": EXTRACTOR,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": "Public source."}],
    }


async def test_resume_does_not_repeat_successful_or_ambiguous_call(tmp_path):
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": '{"facts":[]}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        ledger = Ledger(tmp_path)
        assert (await ledger.call(client, "one", payload(), "not-a-real-secret"))["content"]
        assert (await Ledger(tmp_path).call(client, "one", payload(), "secret"))["content"]
        assert len(seen) == 1
        ledger.reserve("ambiguous", payload())
        assert await Ledger(tmp_path).call(client, "ambiguous", payload(), "secret") is None
        assert len(seen) == 1
    assert "not-a-real-secret" not in "".join(p.read_text() for p in tmp_path.glob("*.json"))


def test_pre_dispatch_budget_request_mismatch_and_oversize_stop(tmp_path):
    ledger = Ledger(tmp_path, max_calls=1)
    ledger.reserve("one", payload())
    with pytest.raises(ValueError, match="budget"):
        ledger.reserve("two", payload())
    with pytest.raises(ValueError, match="changed"):
        ledger.reserve("one", {**payload(), "max_tokens": 100})
    with pytest.raises(ValueError):
        reserve_cost({**payload(), "messages": [{"content": "x" * 33000}]})
    with pytest.raises(ValueError):
        reserve_cost({**payload(), "model": "unpriced-model"})
    assert Ledger(tmp_path).reserved > 0


async def test_truncated_response_is_charged_and_never_parsed_as_success(tmp_path):
    response = {
        "choices": [{"finish_reason": "length", "message": {"content": "{}"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4096},
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))
    ) as client:
        row = await Ledger(tmp_path).call(client, "truncated", payload(), "secret")
    assert row["error"] and row["content"] is None
    assert row["actual_rmb"] > 0
    assert len(list(tmp_path.glob("*.launch.json"))) == 1
    assert json.loads(next(tmp_path.glob("*.result.json")).read_text())["usage"]


async def test_source_inventory_precedes_extraction_and_failed_arm_stays_in_coverage(
    tmp_path, monkeypatch
):
    from eval import source_role_run as run
    from eval.source_role_eval import digest

    doc = {"id": "d1", "title": "A", "text": "A uses B.", "dataset": "test", "split": "holdout"}
    run.write_new(tmp_path / "sources.json", [doc])
    run.write_new(
        tmp_path / "frozen.json", {"code": run.code_hashes(), "sources_sha256": digest([doc])}
    )
    requests = []

    def respond(request):
        body = json.loads(request.content)
        data = json.loads(body["messages"][1]["content"])
        requests.append(data)
        if len(requests) == 1:
            assert list(data) == ["source"]
            value = {"references": [{"id": "r1", "claim": "A uses B", "q": doc["text"]}]}
        elif "candidates" in data:
            value = {
                "facts": [
                    {"id": f["id"], "verdict": "supported", "reason": "Stated", "matches": ["r1"]}
                    for f in data["candidates"]
                ],
                "references": [{"id": "r1", "verdict": "supported", "reason": "Stated"}],
            }
        else:
            value = {
                "facts": [
                    {
                        "s": "A",
                        "p": "uses",
                        "o": "B",
                        "n": "positive",
                        "q": "invented" if len(requests) == 3 else doc["text"],
                    }
                ]
            }
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        run.httpx,
        "AsyncClient",
        lambda **kw: real_client(**kw, transport=httpx.MockTransport(respond)),
    )
    await run.execute(tmp_path, "secret")
    await run.execute(tmp_path, "secret")
    assert len(requests) == 5
    summary = run.summarize(tmp_path)
    assert not summary["complete_comparison"]
    assert summary["groups"]["holdout:baseline"]["provisional_reference_coverage"] == 1
    assert summary["groups"]["holdout:roles"]["reference_units"] == 1
    assert summary["groups"]["holdout:roles"]["provisional_reference_coverage"] == 0
    assert summary["attempts"] == 5


def test_run_lock_rejects_a_concurrent_runner(tmp_path):
    from eval.source_role_run import run_lock

    with run_lock(tmp_path), pytest.raises(BlockingIOError), run_lock(tmp_path):
        pytest.fail("concurrent run unexpectedly acquired lock")
