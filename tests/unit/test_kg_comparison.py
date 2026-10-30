import json

import pytest

from eval import kg_comparison as kg


def doc(ident="d1", dataset="hotpot"):
    return {
        "id": ident,
        "dataset": dataset,
        "title": "Alpha",
        "text": "Alpha uses Beta.",
        "question": "DO NOT SEND QUESTION",
        "answers": ["SECRET ANSWER"],
    }


def test_construction_never_sends_eval_fields_and_uses_same_budget():
    for arm in ("direct", "source_repair", "context_repair"):
        req = kg.construction_request("qwen3.8-27b", doc(), arm, [], [doc("d2")], [])
        raw = json.dumps(req)
        assert "SECRET ANSWER" not in raw and "DO NOT SEND QUESTION" not in raw
        assert req["max_tokens"] == 1536
        data = json.loads(req["messages"][1]["content"])
        assert data["source"]["text"] == doc()["text"]
        if arm == "source_repair":
            assert not data["related_sources"] and not data["graph_hints"]


def test_selection_is_balanced_deterministic_and_independent_of_input_order():
    docs = [doc(str(i), d) for d in ("hotpot", "musique", "drop", "squad") for i in range(20)]
    for d in docs:
        d["id"] = d["dataset"] + d["id"]
    a = kg.select_sources(docs)
    assert len(a) == 64
    assert a == kg.select_sources(list(reversed(docs)))
    assert all(
        sum(v["dataset"] == d for v in a) == 16 for d in ("hotpot", "musique", "drop", "squad")
    )


def test_blind_fact_judgments_reject_missing_duplicate_and_unknown_ids():
    good = {
        "facts": [{"id": "x", "verdict": "supported", "matches": ["r1"], "reason": "Entailed."}]
    }
    assert (
        kg.parse_fact_audit(json.dumps(good), {"x"}, {"r1"})["facts"][0]["verdict"] == "supported"
    )
    for bad in (
        {"facts": []},
        {"facts": good["facts"] * 2},
        {"facts": [dict(good["facts"][0], matches=["unknown"])]},
    ):
        with pytest.raises(ValueError):
            kg.parse_fact_audit(json.dumps(bad), {"x"}, {"r1"})


def test_same_source_quotes_do_not_set_semantic_support():
    record = kg.fact_record(
        {
            "error": None,
            "content": json.dumps(
                {
                    "facts": [
                        {
                            "s": "Alpha",
                            "p": "created_by",
                            "o": "Beta",
                            "n": "positive",
                            "q": "Alpha uses Beta.",
                            "qualifiers": [],
                        }
                    ]
                }
            ),
        },
        doc(),
    )
    assert len(record["facts"]) == 1
    assert "supported" not in record
