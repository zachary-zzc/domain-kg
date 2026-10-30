from eval.public_qa_metrics import aggregate, parse_completion, score_case


def case(**updates):
    return {
        "id": "x",
        "dataset": "example",
        "difficulty": "two-hop",
        "stratum": "s",
        "answerable": True,
        "answers": [["New York"], ["NYC"]],
        "support": ["p0", "p1"],
        "contexts": [{"id": "p0"}, {"id": "p1"}],
        **updates,
    }


def test_alias_answer_and_missing_second_citation_are_separate():
    c = case()
    answer = parse_completion('{"answer":"NYC","citations":["p0"],"abstain":false}', c)
    scores = score_case(c, answer)
    assert scores["em"] == scores["f1"] == 1
    assert scores["citation_complete"] == 0
    assert scores["citation_recall"] == 0.5


def test_invalid_json_unknown_refs_and_false_refusals_do_not_pass():
    c = case()
    for raw in [
        "NYC",
        '{"answer":"NYC","citations":["other"],"abstain":false}',
        '{"answer":"NYC","citations":[],"abstain":true}',
    ]:
        assert parse_completion(raw, c) is None
    refused = {"answer": "", "citations": [], "abstain": True}
    assert score_case(c, refused)["em"] == 0
    assert score_case(case(answerable=False, answers=[], support=[]), refused)["em"] == 1


def test_numbers_and_multi_span_use_exact_numeric_values():
    c = case(dataset="drop", answers=[["-2", "New York"]])
    assert (
        score_case(c, {"answer": ["New York", "-2.0"], "citations": [], "abstain": False})["em"]
        == 1
    )
    assert (
        score_case(c, {"answer": ["New York", "2"], "citations": [], "abstain": False})["em"] == 0
    )


def test_missing_rows_remain_in_planned_denominator():
    cases = [case(), case(id="y")]
    result = aggregate(
        cases,
        [
            {
                "id": "x",
                "model": "m",
                "scores": {"em": 1, "f1": 1, "citation_complete": 1},
                "error": None,
            }
        ],
        ["m"],
    )
    assert result["m"]["planned"] == 2
    assert result["m"]["em"] == 0.5
    assert result["m"]["coverage"] == 0.5
