import asyncio

import httpx
import pytest

from eval.public_qa_run import Budget, bounded_call, eligible_models, remaining_cases, verify_frozen


async def test_deadline_covers_entire_http_operation():
    async def delayed(request):
        await asyncio.sleep(0.1)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(delayed)) as client:
        result = await bounded_call(
            client, "https://dashscope.aliyuncs.com/x", {}, "secret", deadline=0.01
        )
    assert result["error"] == "timeout"


def test_budget_uses_observed_usage_and_unknown_is_not_zero():
    budget = Budget(max_calls=2, max_input=100, max_output=30, max_cost=1)
    budget.add("qwen3.8-27b", {"prompt_tokens": 20, "completion_tokens": 40})
    assert budget.reason() == "output_token_limit"
    budget = Budget(max_cost=1)
    budget.add("qwen3.8-27b", None)
    assert budget.unknown_usage == 1
    assert budget.reserved_unknown_cost == 1
    assert budget.reason() == "cost_limit"


def test_frozen_manifest_rejects_changed_artifact(tmp_path):
    (tmp_path / "case").write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        verify_frozen(tmp_path, {"case": "wrong-hash"})


def test_preflight_requires_all_five_usable_responses_without_scoring_on_gold():
    rows = [{"model": "m", "id": str(i), "error": None} for i in range(5)]
    assert eligible_models(rows, ["m", "missing"]) == ["m"]
    rows[0]["error"] = "timeout"
    assert eligible_models(rows, ["m"]) == []


def test_unreported_free_model_usage_stays_unknown_but_cannot_block_priced_models():
    budget = Budget()
    budget.add("deepseek-r1-distill-qwen-1.5b", None)
    assert budget.unknown_usage == 1
    assert budget.reason() is None


def test_resume_never_retries_failed_pairs_or_drops_their_rows():
    cases = [{"id": "a"}, {"id": "b"}]
    rows = [{"id": "a", "model": "m", "error": "http_400"}]
    assert remaining_cases(cases, rows, "m") == [{"id": "b"}]
    assert rows[0]["error"] == "http_400"
    with pytest.raises(ValueError, match="duplicate"):
        remaining_cases(cases, rows + rows, "m")
