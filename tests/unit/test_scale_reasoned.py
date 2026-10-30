import json

from eval import scale_reasoned as scale
from eval.scale_retrieval import blind_request


def test_reasoned_assessment_keeps_blinding_and_limits_only_per_request():
    packet = {
        "question": "How many?",
        "answers": [["SECRET-GOLD"]],
        "evidence": {"sources": [{"id": "s", "text": "Source"}], "paths": []},
    }
    payload = scale.reasoned(blind_request(packet, {"answer": "3"}))
    assert payload["model"] == scale.MODEL
    assert payload["enable_thinking"] is True
    assert payload["thinking_budget"] == 4096
    assert "SECRET-GOLD" not in json.dumps(payload)
    assert "reasoning_effort" not in payload


def test_explicit_negative_evidence_can_resolve_a_false_premise_question():
    time_case = next(row for row in scale.fixtures() if row[0]["id"] == "time")
    assert time_case[2]["sufficiency"] == "sufficient"
    assert time_case[2]["reader_support"] == "unsupported"
    assert time_case[2]["semantic"] == "incorrect"
