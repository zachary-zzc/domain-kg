import json

import pytest

from eval.public_qa_data import (
    normalize_drop,
    normalize_hotpot,
    normalize_musique,
    normalize_squad,
    request_for,
    select_cases,
)


def test_squad_keeps_impossible_and_does_not_leak_gold():
    raw = {
        "data": [
            {
                "title": "Example",
                "paragraphs": [
                    {
                        "context": "A red door.",
                        "qas": [
                            {
                                "id": "x",
                                "question": "What color?",
                                "is_impossible": True,
                                "answers": [],
                                "plausible_answers": [{"text": "red"}],
                            }
                        ],
                    }
                ],
            }
        ]
    }
    case = list(normalize_squad(raw, "dev"))[0]
    assert case["answerable"] is False
    assert case["answers"] == []
    payload = request_for("qwen3.8-27b", case)
    assert "plausible_answers" not in json.dumps(payload)
    assert set(json.loads(payload["messages"][-1]["content"])) == {"question", "sources"}


def test_hotpot_support_must_resolve_to_exact_sentence():
    raw = {
        "_id": "h",
        "question": "Where?",
        "answer": "place",
        "type": "bridge",
        "level": "hard",
        "context": [["A", ["First.", "Second."]]],
        "supporting_facts": [["A", 1]],
    }
    case = normalize_hotpot(raw)
    assert case["support"] == ["p0s1"]
    assert case["contexts"][1]["text"] == "Second."
    raw["supporting_facts"] = [["A", 2]]
    with pytest.raises(ValueError, match="support"):
        normalize_hotpot(raw)


def test_musique_unanswerable_is_not_inferred_from_partial_support():
    raw = {
        "id": "2hop__x",
        "question": "Where?",
        "answer": "secret-gold",
        "answer_aliases": [],
        "answerable": False,
        "paragraphs": [
            {"idx": 0, "title": "A", "paragraph_text": "Partial.", "is_supporting": True}
        ],
    }
    case = normalize_musique(raw)
    assert case["answers"] == [] and case["support"] == []
    assert "secret-gold" not in json.dumps(request_for("qwen3.8-27b", case))


def test_selection_is_deterministic_and_question_disjoint():
    cases = [{"id": str(i), "question": f"Question {i}?", "stratum": "s"} for i in range(8)]
    pilot, formal = select_cases(cases, {"s": 3}, pilot_ids={"0"})
    assert pilot[0]["id"] == "0" and len(formal) == 3
    assert select_cases(list(reversed(cases)), {"s": 3}, {"0"}) == (pilot, formal)
    dup = dict(cases[1], id="duplicate")
    with pytest.raises(ValueError, match="insufficient"):
        select_cases([cases[0], cases[1], dup], {"s": 2}, {"0"})


def test_musique_full_variants_keep_distinct_record_ids():
    raw = {
        "id": "2hop__pair",
        "question": "Which city?",
        "answer": "Paris",
        "answer_aliases": [],
        "answerable": True,
        "paragraphs": [{"idx": 0, "title": "A", "paragraph_text": "Paris.", "is_supporting": True}],
    }
    complete = normalize_musique(raw)
    incomplete = normalize_musique({**raw, "answerable": False})
    assert complete["id"] != incomplete["id"]


def test_drop_query_ids_are_scoped_to_their_passage():
    qa = {
        "query_id": "reused",
        "question": "How many?",
        "answer": {"number": "2", "spans": [], "date": {}},
    }
    raw = {t: {"passage": "Two.", "qa_pairs": [qa]} for t in ["A", "B"]}
    cases = list(normalize_drop(raw))
    assert cases[0]["id"] != cases[1]["id"]
