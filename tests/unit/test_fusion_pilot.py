"""Independent recall and a filter must not silently replace or invent evidence."""

import importlib
import json

import pytest


def api():
    return importlib.import_module("eval.fusion_pilot")


def chunks():
    return [
        {"id": "a", "document": "a", "text": "Project Nova uses Test Delta."},
        {"id": "b", "document": "b", "text": "Test Delta measures Signal Moss."},
        {"id": "c", "document": "c", "text": "Signal Moss is stored at Site Elm."},
        {"id": "d", "document": "d", "text": "A different project."},
    ]


def test_graph_recall_uses_query_seeds_and_reaches_beyond_vector_hits():
    graph = {"a": {"b": 1}, "b": {"a": 1, "c": 1}, "c": {"b": 1}, "d": {}}
    result = api().graph_recall(chunks(), graph, "Project Nova")
    assert result["seeds"][0] == "a"
    assert "c" in result["selected"]
    assert result["depths"]["c"] == 2
    limited = api().graph_recall(chunks(), graph, "Project Nova", max_nodes=2, max_edges=1)
    assert len(limited["depths"]) <= 2 and limited["edges_examined"] <= 1
    graph["a"]["outside-kg"] = 100
    with pytest.raises(ValueError):
        api().graph_recall(chunks(), graph, "Project Nova")


def test_merge_keeps_vector_baseline_and_records_both_branch_ranks():
    result = api().candidate_pool(
        "local", "Which site?", chunks(), ["a", "d", "b", "c"], ["c", "b", "a"]
    )
    assert result["baseline"] == ["a", "d", "b"]
    assert set(result["baseline"]) <= set(result["selected"])
    assert "c" in result["selected"]
    assert result["provenance"]["b"] == {"vector_rank": 3, "graph_rank": 2}
    request = result["request"]
    assert "expected" not in request["messages"][1]["content"]
    assert api().request_fits(request)


def test_long_candidate_is_omitted_whole_without_dropping_baseline():
    rows = chunks() + [{"id": "long", "document": "long", "text": "x" * 3600}]
    result = api().candidate_pool("local", "Which site?", rows, ["a", "d", "b"], ["long", "c"])
    assert "long" in result["omitted"] and "long" not in result["selected"]
    assert "c" in result["selected"]
    assert result["baseline"] == ["a", "d", "b"]


@pytest.mark.parametrize("selection", [["outside"], ["a", "a"], ["a", "b", "c", "d"], []])
def test_invalid_or_empty_filter_falls_back_to_vector(selection):
    result = api().apply_filter({"selected": selection}, ["a", "b", "c"], chunks(), ["a", "d"])
    assert result["fallback"] is True and result["selected"] == ["a", "d"]


def test_valid_chain_selection_only_returns_original_passages():
    result = api().apply_filter(
        {"selected": ["a", "b", "c"]}, ["a", "b", "c"], chunks(), ["a", "d"]
    )
    assert result == {"selected": ["a", "b", "c"], "fallback": False, "reason": None}
    request = api().filter_request("local", "Where?", chunks(), ["a", "d"])
    assert request["max_completion_tokens"] == 128
    assert json.loads(request["messages"][1]["content"])["vector_baseline"] == ["a", "d"]


def test_oversized_filter_selection_falls_back_without_truncating_source():
    rows = chunks() + [{"id": "large", "document": "x", "text": "x" * 1801}]
    result = api().apply_filter({"selected": ["large"]}, ["large"], rows, ["a"])
    assert result["fallback"] is True and result["selected"] == ["a"]
