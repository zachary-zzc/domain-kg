from eval import scale_analysis as scale


def test_costs_separate_construction_from_reader_and_judge(tmp_path):
    from eval.source_role_run import write_new

    directories = [tmp_path / "calls", tmp_path / "evaluation-calls"]
    for directory, label in zip(
        directories, ["construction/model/doc:0", "blind/hash:0"], strict=True
    ):
        write_new(directory / "x.launch.json", {"label": label, "payload": {"model": "m"}})
        write_new(directory / "x.result.json", {"actual_rmb": 1, "usage": {}, "error": None})
    rows = scale.accounting(directories)
    assert len(rows) == 2
    assert {r["stage"] for r in rows} == {"construction", "blind"}
    assert sum(r["known_rmb"] for r in rows) == 2


def record(ident, *, answerable=True, semantic="correct", support="supported", valid=True):
    return {
        "id": ident,
        "dataset": "test",
        "answerable": answerable,
        "diagnostics": {
            "reference_coverage": {"complete": 1 if answerable else None},
            "retrieval_seconds": 0.1,
            "source_count": 2,
            "content_chars": 100,
        },
        "result": {
            "reader": {"protocol_valid": True, "abstain": False},
            "blind": {
                "reader_support": support,
                "sufficiency": "sufficient",
                "relevance": "useful",
                "citation_support": "supported",
            },
            "semantic": {"semantic": semantic} if valid else None,
            "errors": {} if valid else {"semantic": "invalid_judgment"},
        },
    }


def test_full_denominators_do_not_drop_failed_judgments_or_mix_noanswer_cases():
    rows = [record("a"), record("b", valid=False), record("c", answerable=False)]
    result = scale.summarize(rows)
    assert result["questions"] == 3
    assert result["answerable_questions"] == 2
    assert result["answerable_semantic_correct"] == 1
    assert result["answerable_semantic_rate"] == 0.5
    assert result["semantic_valid"] == 2
    assert result["answerable_semantic_unresolved"] == 1


def test_correct_guess_is_separate_from_supported_retrieval_answer():
    result = scale.summarize([record("a", support="unsupported")])
    assert result["answerable_semantic_rate"] == 1
    assert result["answerable_supported_rate"] == 0


def test_paired_comparison_uses_question_identity_and_only_valid_pairs():
    base = [record("a", semantic="incorrect"), record("b"), record("c", valid=False)]
    graph = [record("b"), record("a"), record("c")]
    result = scale.paired(base, graph)
    assert result["paired_questions"] == 2
    assert result["gain"] == 1 and result["loss"] == 0
    assert result["delta"] == 0.5
    assert result["excluded_unresolved"] == 1


def test_abstention_is_not_counted_as_an_answer_with_supported_citations():
    answered = record("a")
    abstained = record("b", semantic="valid_abstention")
    abstained["result"]["reader"]["abstain"] = True
    result = scale.summarize([answered, abstained])
    assert result["answered_count"] == 1
    assert result["citation_supported_answered_count"] == 1
    assert result["citation_supported_among_answered"] == 1


def test_original_noanswer_cases_include_newly_supported_answers_and_abstentions():
    rows = [
        record("a", answerable=False),
        record("b", answerable=False, semantic="valid_abstention", support="abstained"),
        record("c", answerable=False, semantic="unverifiable", support="unsupported"),
        record("d", answerable=False, valid=False),
    ]
    result = scale.summarize(rows)
    assert result["original_noanswer_questions"] == 4
    assert result["original_noanswer_semantic_labels"] == {
        "correct": 1,
        "valid_abstention": 1,
        "unverifiable": 1,
        "unresolved": 1,
    }
    assert result["original_noanswer_supported_correct"] == 1
    assert result["all_semantic_success_count"] == 2
    assert result["all_semantic_success_rate"] == 0.5


def test_missing_evidence_assessment_remains_unresolved_even_with_correct_semantics():
    missing = record("missing")
    missing["result"]["blind"] = None
    values = scale.summarize([record("ok"), missing])
    assert values["answerable_semantic_unresolved"] == 0
    assert values["answerable_blind_unresolved"] == 1
    assert values["answerable_supported_unresolved"] == 1
    assert values["answerable_supported_rate"] == 0.5
    assert values["answerable_supported_upper_bound"] == 1


def test_evidence_comparison_does_not_require_a_successful_answer_judgment():
    base = record("a", valid=False)
    base["result"]["blind"]["sufficiency"] = "partial"
    graph = record("a", valid=False)
    values = scale.paired([base], [graph], metric="sufficient")
    assert values["paired_questions"] == 1
    assert values["gain"] == 1
    assert values["delta"] == 1
    assert scale.paired([base], [graph])["paired_questions"] == 0


def test_costs_deduplicate_copied_launches_but_count_new_identical_requests(tmp_path):
    from eval.source_role_run import write_new

    directories = [tmp_path / "old" / "calls", tmp_path / "new" / "calls"]
    launch = {"label": "reader/same:0", "payload": {"model": "m"}, "launched_at": 1}
    result = {"actual_rmb": 2, "usage": {}, "error": None}
    for directory in directories:
        write_new(directory / "x.launch.json", launch)
        write_new(directory / "x.result.json", result)
    write_new(directories[1] / "y.launch.json", {**launch, "launched_at": 2})
    write_new(directories[1] / "y.result.json", result)
    rows = scale.accounting(directories)
    assert sum(row["attempts"] for row in rows) == 2
    assert sum(row["known_rmb"] for row in rows) == 4
