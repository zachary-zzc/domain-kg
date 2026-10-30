import pytest

from eval.semantic_summary import common_questions, paired_changes, statistics, validate_review


def pair(q, model, arm, group, **extra):
    return {
        "identity": f"{q}-{model}-{arm}",
        "case_id": q,
        "model": model,
        "arm": arm,
        "group": group,
        "citation": {
            "complete": 0,
            "recall": 0,
            "precision": 0,
            "status": "missing",
            "recovery": None,
            "invalid_ids": [],
        },
        "old_scores": None,
        "original_error": "failed",
        **extra,
    }


def test_common_cohort_uses_completion_not_correctness_or_transport_success():
    rows = [pair("q", m, a, "g") for m in ("m1", "m2") for a in ("V6", "U12")]
    rows.append(pair("tail", "m1", "V6", "g"))
    assert common_questions(rows, ("m1", "m2"), ("V6", "U12")) == {"q"}


def test_duplicate_logical_response_is_rejected():
    row = pair("q", "m", "V6", "g")
    with pytest.raises(ValueError, match="duplicate"):
        common_questions([row, row], ("m",), ("V6",))


def test_citations_and_content_have_independent_denominators_and_ambiguity_bound():
    rows = [pair("q1", "m", "V6", "correct"), pair("q2", "m", "V6", "wrong")]
    rows[1]["citation"].update(complete=1, recall=1, precision=1, status="ok")
    rows.append(pair("q3", "m", "V6", "partial"))
    rows[2]["citation"]["status"] = "ambiguous"
    s = statistics(
        rows, {k: {"judgment": v} for k, v in (("correct", "c"), ("wrong", "w"), ("partial", "p"))}
    )
    assert s["n"] == 3
    assert s["content_accuracy"] == 1 / 3
    assert s["citation_complete_lower"] == 1 / 3
    assert s["citation_complete_upper"] == 2 / 3
    assert s["correct_and_complete"] == 0


def test_paired_changes_never_compare_different_question_sets():
    rows = [
        pair("q1", "m", "V6", "w"),
        pair("q1", "m", "U12", "c"),
        pair("q2", "m", "V6", "c"),
        pair("q2", "m", "U12", "w"),
        pair("tail", "m", "U12", "c"),
    ]
    labels = {x: {"judgment": x} for x in ("c", "w")}
    result = paired_changes(rows, labels, {"q1", "q2"}, "m", "V6", "U12")
    assert (result["wins"], result["losses"], result["n"], result["delta"]) == (1, 1, 2, 0)
    with pytest.raises(ValueError, match="missing"):
        paired_changes(rows, labels, {"q1", "tail"}, "m", "V6", "U12")


def test_review_requires_exact_member_and_question_quality_coverage():
    row = pair("q", "m", "V6", "g")
    packets = [{"id": "q", "groups": [{"key": "g", "members": [row["identity"]]}]}]
    quality = {"q": {"reviewed": True, "eligible_primary": True}}
    validate_review(packets, [row], {"g": {"judgment": "c"}}, quality)
    with pytest.raises(ValueError, match="judgment"):
        validate_review(packets, [row], {}, quality)
    with pytest.raises(ValueError, match="quality"):
        validate_review(packets, [row], {"g": {"judgment": "c"}}, {})
    with pytest.raises(ValueError, match="different question"):
        validate_review(packets, [{**row, "case_id": "other"}], {"g": {"judgment": "c"}}, quality)
