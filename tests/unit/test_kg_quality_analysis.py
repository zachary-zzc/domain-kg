from eval.kg_quality_analysis import summarize_quality


def test_empty_graph_is_zero_coverage_not_perfect_precision():
    rows = [
        {
            "facts": 0,
            "judged": 0,
            "supported": 0,
            "covered": 0,
            "reference_count": 3,
            "error": None,
            "nonempty": False,
            "rejected": 0,
            "raw_count": 0,
        }
    ]
    result = summarize_quality(rows)
    assert result["support_rate"] is None
    assert result["coverage_rate"] == 0
    assert result["nonempty_sources"] == 0


def test_unknown_judgments_are_visible_and_not_treated_as_semantically_wrong():
    rows = [
        {
            "facts": 3,
            "judged": 2,
            "supported": 1,
            "covered": 1,
            "reference_count": 2,
            "error": None,
            "nonempty": True,
            "rejected": 0,
            "raw_count": 3,
        }
    ]
    result = summarize_quality(rows)
    assert result["support_rate"] == 0.5
    assert result["unresolved_facts"] == 1
    assert result["support_lower"] == 1 / 3
    assert result["support_upper"] == 2 / 3
    assert result["coverage_rate"] == 0.5


def test_inventory_failure_does_not_disappear_as_zero_reference_coverage():
    rows = [
        {
            "facts": 1,
            "judged": 1,
            "supported": 1,
            "covered": None,
            "reference_count": None,
            "error": None,
            "nonempty": True,
            "rejected": 0,
            "raw_count": 1,
        }
    ]
    result = summarize_quality(rows)
    assert result["coverage_sources"] == 0
    assert result["coverage_rate"] is None


def test_historical_graph_without_attempt_metadata_does_not_claim_zero_failures():
    rows = [
        {
            "facts": 1,
            "judged": 1,
            "supported": 1,
            "covered": 1,
            "reference_count": 1,
            "error": None,
            "nonempty": True,
            "rejected": 0,
            "raw_count": 1,
            "construction_metadata_available": False,
        }
    ]
    result = summarize_quality(rows)
    assert result["accepted_facts"] == 1
    assert result["construction_errors"] is None
    assert result["rejected_proposals"] is None
    assert result["raw_proposals_valid_envelopes"] is None


def test_unresolved_facts_keep_possible_inventory_coverage_visible():
    rows = [
        {
            "facts": 2,
            "judged": 1,
            "supported": 1,
            "covered": 1,
            "reference_count": 3,
            "error": None,
            "nonempty": True,
            "rejected": 0,
            "raw_count": 2,
        }
    ]
    result = summarize_quality(rows)
    assert result["coverage_rate"] == 1 / 3
    assert result["coverage_upper"] == 1
