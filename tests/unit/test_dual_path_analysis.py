from eval.dual_path_analysis import common_cohort, group_scores, paired, wilson


def test_missing_work_is_na_and_noanswer_is_separate():
    cases = {"a": {"answerable": True}, "b": {"answerable": False}}
    records = [{"id": k, "retrieval": {"complete": 1, "recall": 1}} for k in cases]
    missing = group_scores(cases, records, {})
    assert missing["answerable_em"] is None
    assert missing["attempted"] == 0
    rows = {
        "a": {
            "error": None,
            "seconds": 1,
            "scores": {
                "em": 1,
                "f1": 1,
                "citation_complete": 0,
                "protocol_ok": 1,
            },
        }
    }
    result = group_scores(cases, records, rows)
    assert result["answerable_em"] == 1
    assert result["citation_complete"] == 0
    assert result["noanswer_accuracy"] is None
    assert result["coverage"] == 0.5


def test_pair_comparison_requires_same_completed_cases():
    v = {"a": 1, "b": 0, "c": 1}
    u = {"a": 1, "b": 1}
    result = paired(v, u)
    assert result["paired"] == 2
    assert result["union_only_correct"] == 1
    assert result["vector_only_correct"] == 0
    assert result["delta"] == 0.5


def test_wilson_interval_does_not_claim_certainty_from_tiny_perfect_set():
    lo, hi = wilson(12, 12)
    assert 0.7 < lo < 0.8
    assert 0.999 < hi <= 1


def test_common_cohort_requires_every_model_arm_without_selecting_for_correctness():
    cases = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    results = {(m, c, a): {} for m in ("m1", "m2") for c in ("a", "b") for a in ("V", "U")}
    results["m2", "b", "U"] = {"error": "invalid_protocol"}
    assert common_cohort(cases, results, ("m1", "m2"), ("V", "U")) == ["a", "b"]
    del results["m2", "b", "U"]
    assert common_cohort(cases, results, ("m1", "m2"), ("V", "U")) == ["a"]
