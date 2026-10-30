import json

import pytest

from eval.source_role_normalize import normalize_evidence_judgment, normalize_judgments


def test_recovery_preserves_long_reason_and_verdict_but_removes_unsupported_credit():
    value = {
        "facts": [{"id": "f", "verdict": "insufficient", "reason": "x" * 1300, "matches": ["r"]}],
        "references": [{"id": "r", "verdict": "supported", "reason": "stated"}],
    }
    parsed, changes = normalize_judgments(json.dumps(value), {"f"}, {"r"})
    assert parsed["facts"][0] == {**value["facts"][0], "matches": []}
    assert {c["kind"] for c in changes} == {"long_reason_preserved", "unsupported_zero_credit"}
    for mutate in (
        lambda v: v["facts"].append(v["facts"][0]),
        lambda v: v["facts"][0].update(matches=["unknown"]),
        lambda v: v["facts"][0].update(permissions="owner"),
        lambda v: v["facts"][0].update(reason="x" * 20001),
    ):
        changed = json.loads(json.dumps(value))
        mutate(changed)
        with pytest.raises(ValueError):
            normalize_judgments(json.dumps(changed), {"f"}, {"r"})


def test_empty_path_normalization_cannot_change_answer_support_or_invalid_citations():
    value = {
        "reference_status": "clear",
        "sufficiency": "partial",
        "answer": "",
        "citations": ["s"],
        "path_status": "faithful_relevant",
        "reader_status": "unsupported",
        "reason": "Missing required bridge",
    }
    normalized, changes = normalize_evidence_judgment(json.dumps(value), {"s"}, False)
    assert normalized == {**value, "path_status": "none"}
    assert changes[0]["original_path_status"] == "faithful_relevant"
    with pytest.raises(ValueError):
        normalize_evidence_judgment(json.dumps(value), {"other"}, False)
    with pytest.raises(ValueError):
        normalize_evidence_judgment(json.dumps({**value, "path_status": "none"}), {"s"}, True)
