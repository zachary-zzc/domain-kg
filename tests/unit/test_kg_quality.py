import copy
import json

import pytest

from eval import kg_quality as quality
from eval.source_role_eval import digest


def corpus():
    return [
        {
            "id": "a",
            "dataset": "test",
            "title": "Book",
            "text": "Ada wrote Blue.",
            "units": [{"id": "u1", "text": "Ada wrote Blue."}],
        },
        {
            "id": "b",
            "dataset": "test",
            "title": "Ada",
            "text": "Ada was born in Rome.",
            "units": [{"id": "u2", "text": "Ada was born in Rome."}],
        },
        {
            "id": "c",
            "dataset": "test",
            "title": "Alternative",
            "text": "The author of Blue was born in Rome.",
            "units": [{"id": "u3", "text": "The author of Blue was born in Rome."}],
        },
    ]


def case():
    return {
        "id": "q",
        "dataset": "test",
        "question": "Where was the author of Blue born?",
        "answers": [["Rome"]],
        "answerable": True,
        "support": ["u1", "u2"],
        "documents": ["a", "b", "c"],
        "stratum": "bridge",
        "difficulty": "two-hop",
    }


def packet():
    return {
        **case(),
        "model": quality.WORK_MODELS[0],
        "requirements": [{"id": "r1", "text": "Identify the birthplace of the author of Blue."}],
        "reference_sources": [{"id": "u1", "text": "SECRET-GOLD"}],
        "evidence": {"sources": [{"id": "c", "text": corpus()[2]["text"]}], "paths": []},
    }


def review():
    return {
        "id": "q",
        "original_sha256": digest(case()),
        "question": case()["question"],
        "decision": "include",
        "track": "multi_hop",
        "reviewer": "fixture-author",
        "reason": "Self-contained; both original bridge facts are available.",
        "requirements": ["Identify the birthplace of the author of Blue."],
    }


def evidence_judgment():
    return {
        "sufficiency": "sufficient",
        "answer": "Rome",
        "citations": ["c"],
        "sources": [{"id": "c", "relevance": "useful"}],
        "reason": "An alternative passage states the answer.",
        "requirements": [{"id": "r1", "coverage": "covered"}],
    }


def qa_judgment():
    return {
        "correctness": "correct",
        "grounding": "supported",
        "citations": "supported",
        "reference_status": "consistent",
        "path_status": "none",
        "reason": "Equivalent answer supported by cited original passage.",
        "requirements": [{"id": "r1", "coverage": "covered"}],
        "claims": [{"claim": "Rome is the requested birthplace.", "grounding": "supported"}],
    }


def test_pending_review_and_changed_original_cannot_enter_frozen_set():
    pending = {**review(), "decision": "pending"}
    with pytest.raises(ValueError, match="pending"):
        quality.reviewed_cases([case()], corpus(), [pending])
    changed = {**case(), "answers": [["Paris"]]}
    with pytest.raises(ValueError, match="changed"):
        quality.reviewed_cases([changed], corpus(), [review()])
    result = quality.reviewed_cases([case()], corpus(), [review()])
    assert result[0]["original_question"] == case()["question"]
    assert result[0]["evaluation_split"] == "development"


def test_rewritten_question_rejects_old_rankings_even_with_same_id():
    ranks = quality.rank_bundle([case()], corpus(), [["a", "b", "c"]])
    changed = {**case(), "question": "Which city was Ada born in?"}
    with pytest.raises(ValueError, match="query"):
        quality.validate_ranks(ranks, [changed], corpus())
    ranks["rows"][0]["documents"] = ["a", "a"]
    with pytest.raises(ValueError, match="duplicate"):
        quality.validate_ranks(ranks, [case()], corpus())


def test_reviewed_annotations_preserve_originals_and_require_existing_support():
    edit = {**review(), "answers": [["Rome, Italy"]], "support": ["u3"]}
    result = quality.reviewed_cases([case()], corpus(), [edit])[0]
    assert result["answers"] == [["Rome, Italy"]]
    assert result["original_answers"] == [["Rome"]]
    assert result["original_support"] == ["u1", "u2"]
    with pytest.raises(ValueError):
        quality.reviewed_cases([case()], corpus(), [{**edit, "support": ["unknown"]}])


def test_alternative_evidence_is_valid_without_matching_reference_ids():
    assert set(packet()["support"]) == {"u1", "u2"}
    assert packet()["evidence"]["sources"][0]["id"] == "c"
    assert (
        quality.parse_evidence(json.dumps(evidence_raw()), packet())["sufficiency"] == "sufficient"
    )


def test_reference_free_judge_sees_neither_reader_nor_gold_nor_arm_label():
    p = packet()
    p["answers"] = [["SECRET-GOLD"]]
    p["reader"] = {"answer": "LEAKED-READER"}
    p["arm"] = "SECRET-ARM"
    payload = quality.evidence_request(p)
    data = json.loads(payload["messages"][1]["content"])
    assert set(data) == {"question", "requirements", "sources"}
    assert all(s not in json.dumps(payload) for s in ("SECRET-GOLD", "LEAKED-READER", "SECRET-ARM"))


def test_judge_requires_every_source_once_and_cannot_invent_citations():
    value = evidence_raw()
    value["sources"] = []
    with pytest.raises(ValueError):
        quality.parse_evidence(json.dumps(value), packet())
    value = evidence_raw()
    value["sources"][0]["relevance"] = "irrelevant"
    with pytest.raises(ValueError):
        quality.parse_evidence(json.dumps(value), packet())
    value = evidence_raw()
    value["citations"] = ["u1"]
    with pytest.raises(ValueError):
        quality.parse_evidence(json.dumps(value), packet())


def test_abstention_cannot_be_judged_correct_or_hide_a_nonempty_answer():
    reader = quality.parse_reader('{"answer":"","citations":[],"abstain":true}', {"c"})
    with pytest.raises(ValueError):
        quality.parse_qa(json.dumps(qa_raw()), packet(), reader)
    malformed = quality.parse_reader('{"answer":"Rome","citations":["c"],"abstain":true}', {"c"})
    assert not malformed["protocol_valid"]
    assert malformed["answer"] == "Rome"  # still available for semantic audit
    missing = qa_raw()
    del missing["claims"]
    with pytest.raises(ValueError):
        quality.parse_qa(json.dumps(missing), packet(), reader)


def test_guessed_correct_answer_is_not_grounded_success_and_unknown_is_not_zero():
    reader = quality.parse_reader('{"answer":"Rome","citations":["c"],"abstain":false}', {"c"})
    grounded = quality.score(reader, evidence_judgment(), qa_judgment())
    assert grounded["grounded_correct"] == 1
    guess = quality.score(
        reader, evidence_judgment(), {**qa_judgment(), "grounding": "unsupported"}
    )
    assert guess["semantic_correct"] == 1
    assert guess["grounded_correct"] == 0
    unknown = quality.score(reader, None, None)
    assert unknown["grounded_correct"] is None
    assert unknown["evidence_sufficient"] is None
    summary = quality.metric_summary([1, 0, None])
    assert summary == {
        "planned": 3,
        "known": 2,
        "unknown": 1,
        "mean_known": 0.5,
        "lower_bound": 1 / 3,
        "upper_bound": 2 / 3,
    }


def test_pairing_reports_losses_wins_and_unknown_pairs_without_selection_bias():
    rows = [
        {"id": "a", "arm": "text", "scores": {"grounded_correct": 0}},
        {"id": "a", "arm": "kg_sources", "scores": {"grounded_correct": 1}},
        {"id": "b", "arm": "text", "scores": {"grounded_correct": 1}},
        {"id": "b", "arm": "kg_sources", "scores": {"grounded_correct": 0}},
        {"id": "c", "arm": "text", "scores": {"grounded_correct": 1}},
    ]
    result = quality.paired(rows, ["a", "b", "c"], "text", "kg_sources", "grounded_correct")
    assert result["wins"] == 1 and result["losses"] == 1
    assert result["unknown_pairs"] == 1 and result["paired_delta"] == 0
    assert result["delta_bounds_all_planned"] == [-1 / 3, 0]
    with pytest.raises(ValueError, match="duplicate"):
        quality.paired(
            rows + [copy.deepcopy(rows[0])],
            ["a", "b", "c"],
            "text",
            "kg_sources",
            "grounded_correct",
        )


def test_semantic_coverage_completeness_claims_and_unknowns():
    p = packet()
    reader = quality.parse_reader('{"answer":"Rome","citations":["c"],"abstain":false}', {"c"})
    qa = qa_judgment()
    qa["requirements"][0]["coverage"] = "partial"
    qa["claims"].append({"claim": "An invented additional fact", "grounding": "unsupported"})
    values = quality.score(reader, evidence_judgment(), qa)
    assert values["semantic_coverage"] == 1
    assert values["answer_completeness"] == 0.5
    assert values["unsupported_claim_rate"] == 0.5
    qa["claims"][0]["grounding"] = "uncertain"
    assert quality.score(reader, None, qa)["unsupported_claim_rate"] is None
    missing = evidence_raw()
    missing["requirements"] = []
    with pytest.raises(ValueError):
        quality.parse_evidence(json.dumps(missing), p)


def test_reader_uses_working_model_and_judges_stay_fixed():
    for model in quality.WORK_MODELS:
        p = {**packet(), "model": model}
        assert quality.reader_request(p)["model"] == model
        assert quality.evidence_request(p)["model"] == quality.JUDGE
        assert quality.qa_request(p, {})["model"] == quality.JUDGE


def test_summary_separates_six_models_and_has_no_literal_reference_metrics():
    c = {**case(), "track": "multi_hop"}
    rows = [
        {
            "id": c["id"],
            "model": quality.WORK_MODELS[0],
            "arm": "vector_rag",
            "scores": {"grounded_correct": 1},
        }
    ]
    summary = quality.summarize([c], rows)
    assert set(summary["models"]) == set(quality.WORK_MODELS)
    assert "support_recall" not in json.dumps(summary)
    for model in quality.WORK_MODELS:
        group = summary["models"][model]["groups"]["all"]
        assert set(group["arms"]) == {"vector_rag", "domain_kg"}
        pair = group["paired"]["grounded_correct"]
        assert pair["known_pairs"] == 0 and pair["losses"] == 0


def test_useful_bridge_with_missing_final_fact_derives_partial_evidence():
    value = evidence_raw()
    value.update(answer="", citations=[])
    value["requirements"][0]["coverage"] = "missing"
    assert quality.parse_evidence(json.dumps(value), packet())["sufficiency"] == "partial"


def test_grounding_uses_all_candidate_claim_support_independent_of_wrong_citation():
    reader = quality.parse_reader('{"answer":"Rome","citations":["c"],"abstain":false}', {"c"})
    value = {**qa_raw(), "citations": "unsupported"}
    assessed = quality.parse_qa(json.dumps(value), packet(), reader)
    assert assessed["grounding"] == "supported"
    assert quality.score(reader, None, assessed)["grounded_correct"] == 1
    assert quality.score(reader, None, assessed)["citation_supported"] == 0


def test_qa_must_not_confuse_arithmetic_reasoning_with_an_explicit_graph_path():
    reader = quality.parse_reader('{"answer":"Rome","citations":["c"],"abstain":false}', {"c"})
    value = {**qa_raw(), "path_status": "faithful_relevant"}
    parsed = quality.parse_qa(json.dumps(value), packet(), reader)
    assert parsed["path_status"] == "uncertain"
    assert quality.score(reader, None, parsed)["grounded_correct"] == 1
    assert (
        "candidate_evidence.paths array"
        in quality.qa_request(packet(), reader)["messages"][0]["content"]
    )


def evidence_raw():
    return {k: v for k, v in evidence_judgment().items() if k != "sufficiency"}


def qa_raw():
    return {k: v for k, v in qa_judgment().items() if k != "grounding"}


def test_partly_relevant_source_contributes_evidence_without_being_sufficient():
    value = evidence_raw()
    value["sources"][0]["relevance"] = "partial"
    value["requirements"][0]["coverage"] = "missing"
    value.update(answer="", citations=[])
    parsed = quality.parse_evidence(json.dumps(value), packet())
    metrics = quality.score(None, parsed, None)
    assert metrics["evidence_sufficient"] == 0
    assert metrics["semantic_source_precision"] == 1
    assert metrics["semantic_coverage"] == 0


def test_omitted_source_ratings_do_not_erase_independent_evidence_sufficiency():
    p = packet()
    p["evidence"]["sources"].append({"id": "extra", "text": "An unrelated paragraph."})
    value = evidence_raw()
    del value["reason"]
    parsed = quality.parse_evidence(json.dumps(value), p)
    scores = quality.score(None, parsed, None)
    assert scores["evidence_sufficient"] == 1
    assert scores["semantic_coverage"] == 1
    assert scores["semantic_source_precision"] is None
    assert parsed["reason"] is None


def test_unlabeled_claims_do_not_erase_independent_citation_assessment():
    reader = quality.parse_reader('{"answer":"Rome","citations":["c"],"abstain":false}', {"c"})
    support = {"citations": "supported", "path_status": "none", "claims": ["Rome"]}
    parsed = quality.parse_support(json.dumps(support), packet(), reader)
    scores = quality.score(reader, None, {**parsed, "correctness": "correct"})
    assert scores["citation_supported"] == 1
    assert scores["grounded_correct"] is None
    assert scores["unsupported_claim_rate"] is None
