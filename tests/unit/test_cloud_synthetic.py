import pytest

from eval.cloud_synthetic import SYSTEM, summary, validate_requests


def test_missing_answers_remain_in_the_planned_denominator():
    questions = [{"id": "q1", "expected": ["k1"]}, {"id": "q2", "expected": []}]
    row = {
        "id": "q1",
        "model": "m",
        "arm": "V3",
        "answer": {"answer": "x"},
        "error": None,
        "metrics": {"answer_correct": True, "citation_complete": True, "supported_correct": True},
    }
    result = summary([row], questions, ["m"])
    assert result["m/V3"]["questions"] == 2
    assert result["m/V3"]["completed"] == 1
    assert result["m/V3"]["answer_accuracy"] == 0.5
    assert result["m/V6"]["completed"] == 0
    assert result["m/V6"]["answer_accuracy"] == 0
    with pytest.raises(ValueError):
        summary([row, row], questions, ["m"])


def test_nonfictional_passage_cannot_be_exported():
    import json

    row = {
        "id": "three_hop1",
        "selected": ["h14"],
        "request": {
            "messages": [
                {"role": "system", "content": SYSTEM},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "question": (
                                "Which cabinet holds the sample preserved by the kit "
                                "actually used by Expedition Sable?"
                            ),
                            "evidence": [{"id": "h14", "text": "Nonfictional external material"}],
                        }
                    ),
                },
            ]
        },
    }
    with pytest.raises(ValueError, match="cannot be exported"):
        validate_requests([row])
