"""Catch contaminated baselines, conditional denominators and false qualification."""

import importlib

import pytest


def api():
    return importlib.import_module("eval.holdout_pilot")


def question():
    return {
        "id": "q",
        "split": "multihop",
        "query": "Where?",
        "expected": ["a", "b", "c"],
        "answers": [["Mica"]],
        "forbidden": [],
    }


def test_pure_vector_ranking_ignores_words_and_vector_magnitude():
    chunks = [
        {"id": "a", "text": "No overlapping words.", "document": "1"},
        {"id": "b", "text": "Exact lexical query match.", "document": "2"},
        {"id": "c", "text": "Other.", "document": "3"},
    ]
    assert api().dense_rank(chunks, [[1, 0], [90, 100], [-1, 0]], [1, 0]) == ["a", "b", "c"]
    graph = {"a": {"c": 1}, "b": {}, "c": {"b": 1}}
    assert api().select(chunks, [[1, 0], [90, 100], [-1, 0]], [1, 0], "V", graph) == ["a", "b", "c"]
    assert api().select(chunks, [[1, 0], [90, 100], [-1, 0]], [1, 0], "D", graph) == ["a", "c", "b"]


def test_correct_answer_with_only_final_link_fails_citation_completeness():
    result = api().score(question(), ["a", "b", "c"], {"answer": "Mica", "citations": ["c"]})
    assert result["answer_correct"] is True
    assert result["citation_complete"] is False
    assert result["supported_correct"] is False


def test_citation_completeness_is_independent_of_answer_correctness():
    result = api().score(
        question(), ["a", "b", "c"], {"answer": "Wrong", "citations": ["a", "b", "c"]}
    )
    assert result["answer_correct"] is False
    assert result["citation_complete"] is True
    assert result["supported_correct"] is False
    invalid = api().score(question(), ["a", "b"], {"answer": "Mica", "citations": ["a", "b", "c"]})
    assert invalid["citation_complete"] is False


def test_unanswerable_question_needs_exact_refusal_and_has_no_citation_denominator():
    q = {**question(), "expected": [], "answers": [["insufficient_evidence"]]}
    refused = api().score(q, ["a"], {"answer": "insufficient_evidence", "citations": []})
    assert refused["answer_correct"] is True
    assert refused["citation_complete"] is None
    wrong = api().score(q, ["a"], {"answer": "Possibly insufficient evidence", "citations": []})
    assert wrong["answer_correct"] is False


def test_missing_answers_stay_in_both_denominators():
    q = question()
    result = api().summaries([], [q])
    assert result["V/all"]["questions"] == 1
    assert result["V/all"]["answer_correct"] == 0
    assert result["V/all"]["citation_questions"] == 1
    assert result["V/all"]["citation_complete"] == 0
    assert result["D/all"]["completed"] == 0
    with pytest.raises(ValueError):
        api().summaries([{"id": "outside", "arm": "V"}], [q])


def test_gate_uses_exact_counts_and_requires_completed_audited_comparison():
    v = {
        "questions": 40,
        "answer_correct": 33,
        "citation_questions": 36,
        "citation_complete": 34,
        "completed": 40,
        "audited": 40,
    }
    d = {**v, "answer_correct": 34, "citation_complete": 33}
    stats = {"V/all": v, "D/all": d}
    assert api().qualifies(stats) is True
    for field, value in (
        ("answer_correct", 33),
        ("citation_complete", 32),
        ("completed", 39),
        ("audited", 39),
    ):
        assert api().qualifies({**stats, "D/all": {**d, field: value}}) is False
    assert api().qualifies({**stats, "V/all": {**v, "answer_correct": 35}}) is False


def test_frozen_file_change_is_detected_before_inference(tmp_path):
    source = tmp_path / "cases.json"
    source.write_text("original")
    manifest = {"cases.json": api().sha256(source)}
    api().check_files(tmp_path, manifest)
    source.write_text("changed")
    with pytest.raises(RuntimeError, match="Frozen"):
        api().check_files(tmp_path, manifest)


def test_semantic_audit_cannot_be_replayed_for_a_different_answer():
    row = {
        "id": "q",
        "arm": "V",
        "selected": ["a", "b", "c"],
        "answer": {"answer": "Mica, and another invented claim", "citations": ["a", "b", "c"]},
        "error": None,
    }
    audit = {"answer_sha256": "stale", "answer_correct": False, "reason": "Unsupported extra claim"}
    with pytest.raises(ValueError, match="audit"):
        api().audited_score(question(), row, audit)


def test_new_run_budget_halts_at_eighty_and_after_two_timeouts():
    runtime = importlib.import_module("eval.holdout_run")
    limits = runtime.HoldoutLimits("http://127.0.0.1:29500/v1")
    for _ in range(80):
        limits.reserve()
    with pytest.raises(RuntimeError, match="budget"):
        limits.reserve()
    other = runtime.HoldoutLimits("http://127.0.0.1:29500/v1")
    other.outcome(timed_out=True)
    other.outcome(timed_out=True)
    with pytest.raises(RuntimeError, match="timeouts"):
        other.reserve()
    with pytest.raises(ValueError):
        runtime.HoldoutLimits("https://external.example/v1")
