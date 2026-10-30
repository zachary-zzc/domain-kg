import json

from eval.public_qa_analysis import independent_scores


def fixture(content, finish="stop"):
    case = {
        "id": "a",
        "dataset": "squad",
        "question": "Color?",
        "answerable": True,
        "answers": [["blue"]],
        "support": ["p0"],
        "contexts": [{"id": "p0"}],
    }
    row = {
        "http_status": 200,
        "response": {"choices": [{"finish_reason": finish, "message": {"content": content}}]},
    }
    return case, row


def test_wrong_citation_does_not_erase_correct_answer_content():
    c, r = fixture(json.dumps({"answer": "blue", "citations": ["nonexistent"], "abstain": False}))
    s = independent_scores(c, r)
    assert s["answer_em"] == s["answer_f1"] == 1
    assert s["citation_complete"] == 0
    assert s["citation_protocol_ok"] == 0


def test_single_exact_json_fence_is_recorded_not_silently_treated_as_strict_json():
    c, r = fixture('```json\n{"answer":"blue","citations":["p0"],"abstain":false}\n```')
    s = independent_scores(c, r)
    assert s["answer_em"] == s["citation_complete"] == 1
    assert s["fence_normalized"] == 1
    assert s["strict_json"] == 0
    c, r = fixture('Explanation {"answer":"blue","citations":["p0"],"abstain":false}')
    assert independent_scores(c, r)["answer_em"] == 0


def test_truncation_or_contradictory_abstention_is_not_repaired():
    text = '{"answer":"blue","citations":["p0"],"abstain":false}'
    c, r = fixture(text, finish="length")
    assert independent_scores(c, r)["answer_em"] == 0
    c, r = fixture(text.replace("false", "true"))
    s = independent_scores(c, r)
    assert s["answer_em"] == 0
    assert s["citation_complete"] == 1
