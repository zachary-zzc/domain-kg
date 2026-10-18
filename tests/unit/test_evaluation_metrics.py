"""Evaluation counts unique supporting materials and records no-answer false positives."""

import math

import pytest

from eval.metrics import score


def test_rank_metrics_use_unique_sources_and_binary_relevance():
    value = score(["irrelevant", "a", "a", "b"], {"a", "b"}, top_k=3)
    assert value["recall"] == 1
    assert value["precision"] == pytest.approx(2 / 3)
    assert value["reciprocal_rank"] == 0.5
    assert value["ndcg"] == pytest.approx(
        (1 / math.log2(3) + 1 / math.log2(4)) / (1 + 1 / math.log2(3))
    )
    assert not value["false_positive"]


def test_no_answer_cases_do_not_become_perfect_recall_on_irrelevant_results():
    assert score([], set(), top_k=10) == {
        "recall": None,
        "precision": 0,
        "reciprocal_rank": 0,
        "ndcg": None,
        "false_positive": False,
    }
    assert score(["a"], set(), top_k=10)["false_positive"]


@pytest.mark.parametrize("value", [0, -1, 51, True])
def test_invalid_metric_cutoffs_fail(value):
    with pytest.raises(ValueError):
        score([], set(), top_k=value)
