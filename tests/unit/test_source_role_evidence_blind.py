import json

import pytest
from test_source_role_evidence import packet

from eval.source_role_evidence_blind import blind_request, parse_blind


def test_blind_judge_never_receives_gold_answers_or_reference_passages():
    value = packet()
    value["answers"] = [["private answer"]]
    value["reference_sources"] = [{"text": "private reference"}]
    request = blind_request(value, "RAG", None)
    assert "private" not in json.dumps(request)
    data = json.loads(request["messages"][1]["content"])
    assert set(data) == {"question", "candidate_evidence", "blind_reader"}
    assert "exact matching" in request["messages"][0]["content"]


def test_false_premise_and_missing_path_are_explicit_not_exact_answer_checks():
    value = {
        "question_status": "inconsistent",
        "sufficiency": "insufficient",
        "answer": "",
        "citations": ["s"],
        "path_status": "none",
        "reader_status": "unsupported",
        "reason": "The requested cities are in different states.",
    }
    assert parse_blind(json.dumps(value), {"s"}, False) == value
    with pytest.raises(ValueError):
        parse_blind(json.dumps({**value, "reference_status": "clear"}), {"s"}, False)
    with pytest.raises(ValueError):
        parse_blind(json.dumps(value), {"other"}, False)
