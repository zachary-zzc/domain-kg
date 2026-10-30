import json

from eval.semantic_review import citation_score, content_fields, group_key


def test_content_is_independent_of_invalid_citations_and_extra_fields():
    raw = '{"answer":"Shia LaBeouf","citations":["bad"],"extra":1}'
    assert content_fields(raw)["answer"] == "Shia LaBeouf"


def test_negation_signs_and_different_questions_never_share_grade():
    assert group_key("q", "10") != group_key("q", "-10")
    assert group_key("q", "does use") != group_key("q", "does not use")
    assert group_key("q", "x") != group_key("r", "x")


def test_complete_answer_not_disqualified_by_abstain_flag():
    result = content_fields('{"answer":"1699","abstain":true}')
    assert result["answer"] == "1699"
    assert result["abstain"] is True


def test_raw_duplicate_or_unfinished_answer_requires_native_review():
    assert content_fields('{"answer":"A","answer":"B"}')["kind"] == "raw"
    assert content_fields('{"answer":"A')["kind"] == "raw"


def test_citations_recover_exact_array_from_broken_outer_object():
    case = {"support": ["global1", "global2"]}
    sources = [{"id": "global1"}, {"id": "global2"}]
    raw = '{"answer":"text with bad "quotes"","citations":["e0","e1"]}'
    result = citation_score(case, sources, raw)
    assert result["complete"] == 1
    assert result["recovery"] == "explicit_array"


def test_conflicting_duplicate_citations_are_not_silently_unioned():
    case, sources = {"support": ["s1", "s2"]}, [{"id": "s1"}, {"id": "s2"}]
    raw = '{"citations":["e0"],"citations":["e1"]}'
    assert citation_score(case, sources, raw)["status"] == "ambiguous"


def test_citation_score_does_not_depend_on_answer_or_eight_item_limit():
    case = {"support": ["s0", "s8"]}
    sources = [{"id": f"s{i}"} for i in range(9)]
    raw = json.dumps({"answer": "wrong", "citations": [f"e{i}" for i in range(9)]})
    score = citation_score(case, sources, raw)
    assert score["complete"] == 1
    assert score["precision"] == 2 / 9
