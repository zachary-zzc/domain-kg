"""Attached units must not hide a correct number or admit a different number."""

import importlib

import pytest


@pytest.mark.parametrize(
    "text,expected",
    [
        ("less than 48mmol/mol", True),
        ("less than 148mmol/mol", False),
        ("less than 48.5mmol/mol", False),
        ("less than -48mmol/mol", False),
        ("less than 4.8e1mmol/mol", True),
        ("less than 48e3mmol/mol", False),
    ],
)
def test_exact_numeric_value_with_attached_unit(text, expected):
    audit = importlib.import_module("eval.hybrid_audit")
    q = {"expected": ["a"], "answers": [["48"], ["less than"]], "forbidden": []}
    assert audit.assess(q, ["a"], {"answer": text, "citations": ["a"]})["answer_match"] is expected


def test_numeric_fix_does_not_change_identifier_answers_or_accept_negated_abstention():
    audit = importlib.import_module("eval.hybrid_audit")
    q = {"expected": ["a"], "answers": [["Flux-7"]], "forbidden": []}
    assert audit.assess(q, ["a"], {"answer": "Flux-7 assay", "citations": ["a"]})["answer_match"]
    q = {"expected": [], "answers": [["insufficient_evidence"]], "forbidden": []}
    assert not audit.assess(q, ["a"], {"answer": "not insufficient_evidence", "citations": []})[
        "answer_match"
    ]
