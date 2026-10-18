"""Scoring and runtime boundaries for explicit local extraction experiments."""

import importlib

import pytest


def api():
    return importlib.import_module("eval.recursive_runtime")


def test_relation_scoring_penalizes_wrong_direction_polarity_and_duplicates():
    gold = [["gland", "produces", "hormone", "positive"]]
    predicted = [
        gold[0],
        gold[0],
        ["hormone", "produces", "gland", "positive"],
        ["gland", "produces", "hormone", "negative"],
    ]
    score = api().relation_score(predicted, gold)
    assert score["tp"] == 1 and score["fp"] == 2 and score["fn"] == 0
    assert score["precision"] == pytest.approx(1 / 3)
    assert score["direction_errors"] == 1 and score["polarity_errors"] == 1


def test_no_answer_metrics_and_failed_calls_do_not_become_perfect_recall():
    assert api().relation_score([], [])["recall"] is None
    assert api().relation_score([["a", "uses", "b", "positive"]], [])["no_answer_fp"]


def test_runtime_requires_loopback_and_enforces_calls_before_network():
    with pytest.raises(ValueError, match="loopback"):
        api().CallLimits(endpoint="https://example.com/v1")
    limits = api().CallLimits(endpoint="http://127.0.0.1:29500/v1", max_calls=1)
    limits.reserve()
    with pytest.raises(RuntimeError, match="budget"):
        limits.reserve()


def test_source_case_loader_preserves_public_source_spans():
    from test_recursive_extraction import source

    chunk, _ = source()
    actual = api().case_chunk({"id": "kept", "blocks": [0, 1]}, chunk)
    assert actual.spans == chunk.spans
    synthetic = api().case_chunk({"id": "synthetic", "lines": ["A uses B."]}, chunk)
    assert synthetic.id != chunk.id
    assert synthetic.spans[0].quote == "A uses B."


def test_failed_cases_still_count_as_missing_reference_facts_in_aggregate():
    from eval.recursive_run import summaries

    records = [{"split": "development", "gold": [["a", "uses", "b", "positive"]], "stages": {}}]
    result = summaries(records)["baseline/all"]
    assert result["failed_cases"] == 1
    assert result["fn"] == 1
    assert result["recall"] == 0


def test_two_consecutive_timeouts_halt_instead_of_spending_the_rest_of_the_budget():
    limits = api().CallLimits(endpoint="http://127.0.0.1:29500/v1")
    limits.outcome(timed_out=True)
    limits.outcome(timed_out=True)
    with pytest.raises(RuntimeError, match="consecutive"):
        limits.reserve()
