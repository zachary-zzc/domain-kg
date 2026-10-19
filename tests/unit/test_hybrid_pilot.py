"""The offline benchmark must not give itself credit for unsupported answers."""

import importlib

import pytest


def api():
    return importlib.import_module("eval.hybrid_pilot")


def chunks():
    return [
        {"id": "a", "document": "1", "text": "Project Aster uses the Flux-7 assay."},
        {"id": "b", "document": "2", "text": "The Flux-7 assay measures marker Kappa."},
        {"id": "c", "document": "3", "text": "Marker Kappa is produced by gland Nera."},
        {"id": "d", "document": "4", "text": "An unrelated appointment."},
    ]


def test_graph_is_query_free_bounded_and_keeps_unrelated_chunks_disconnected():
    graph = api().build_graph(chunks())
    assert "b" in graph["a"] and "d" not in graph["a"]
    mass = api().diffuse(graph, {"a": 1.0}, steps=1)
    assert mass.get("b", 0) > 0 and mass.get("c", 0) == 0
    assert api().diffuse(graph, {"a": 1.0}, steps=2).get("c", 0) > 0
    with pytest.raises(ValueError):
        api().diffuse(graph, {"a": 1.0}, steps=3)


def test_grounding_checks_quote_and_endpoints_but_does_not_claim_semantic_truth():
    fact = {
        "subject": "Project Aster",
        "predicate": "uses",
        "target": "Flux-7 assay",
        "polarity": "positive",
        "quote": chunks()[0]["text"],
    }
    grounded = api().ground_fact(chunks()[0], fact)
    assert grounded["validation"] == "span_only_pending_review"
    with pytest.raises(ValueError):
        api().ground_fact(chunks()[0], dict(fact, quote="Invented source"))
    with pytest.raises(ValueError):
        api().ground_fact(chunks()[0], dict(fact, target="Vega-2 assay"))


def test_only_explicitly_reviewed_positive_relations_augment_semantic_graph():
    rows = chunks()
    facts = [
        {
            "chunk_id": "a",
            "subject": "Project Aster",
            "target": "Flux-7 assay",
            "polarity": "positive",
            "validation": "agent_reviewed_supported",
        },
        {
            "chunk_id": "b",
            "subject": "Flux-7 assay",
            "target": "marker Kappa",
            "polarity": "negative",
            "validation": "agent_reviewed_supported",
        },
    ]
    plain = api().build_graph(rows)
    assert api().build_graph(rows, facts) == plain
    facts[1]["polarity"] = "positive"
    assert api().build_graph(rows, facts)["a"]["b"] > plain["a"]["b"]
    facts[1]["validation"] = "span_only_pending_review"
    with pytest.raises(ValueError):
        api().build_graph(rows, facts)


def test_failure_is_wrong_and_missing_evidence_cannot_become_supported_answer():
    q = {"expected": ["a", "b"], "answers": [["nera"]], "forbidden": ["luma"]}
    result = api().assess(q, ["a"], {"answer": "Nera", "citations": ["a"]})
    assert result["answer_match"] and not result["supported_correct"]
    assert result["recall"] == 0.5
    assert not api().assess(q, ["a", "b"], None)["answer_match"]
    assert not api().assess(q, ["a", "b"], {"answer": "Luma", "citations": ["a", "b"]})[
        "answer_match"
    ]


def test_out_of_context_and_duplicate_citations_are_invalid():
    q = {"expected": ["a"], "answers": [["nera"]], "forbidden": []}
    for refs in (["a", "b"], ["a", "a"]):
        result = api().assess(q, ["a"], {"answer": "Nera", "citations": refs})
        assert not result["supported_correct"] and not result["citation_valid"]


def test_no_answer_requires_explicit_abstention_and_empty_citations():
    q = {"expected": [], "answers": [["insufficient_evidence"]], "forbidden": []}
    good = api().assess(q, ["a"], {"answer": "insufficient_evidence", "citations": []})
    assert good["recall"] is None and good["supported_correct"]
    assert not api().assess(q, ["a"], None)["supported_correct"]
    assert not api().assess(q, ["a"], {"answer": "Nera", "citations": ["a"]})["answer_match"]


def test_context_budget_preserves_complete_original_chunks_and_fixed_top_k():
    selected = api().fit_context(["a", "b", "c", "d"], chunks(), top_k=2, max_chars=80)
    assert selected == ["a", "b"]
    assert sum(len(row["text"]) for row in chunks() if row["id"] in selected) <= 80


def test_lexical_identifiers_and_deduplicated_rrf():
    assert api().lexical_rank(chunks(), "Flux-7")[0] in ("a", "b")
    fused = api().rrf(["a", "b"], ["b", "a"])
    assert len(fused) == 2


def test_summary_preserves_failed_answer_denominator():
    results = [
        {
            "arm": "A",
            "split": "test",
            "metrics": {
                "recall": 1.0,
                "complete_support": True,
                "answer_match": False,
                "supported_correct": False,
                "citation_valid": False,
            },
            "error": "TimeoutError",
        }
    ]
    report = api().summarize(results)["A/test"]
    assert report["queries"] == 1 and report["failed_answers"] == 1
    assert report["answer_match"] == 0 and report["recall"] == 1
