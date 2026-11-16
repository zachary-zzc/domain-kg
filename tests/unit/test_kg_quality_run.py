import json

import pytest

from eval import kg_quality as quality
from eval import kg_quality_run as run
from eval.kg_quality_graph import build_index


def docs():
    return [
        {
            "id": "a",
            "dataset": "test",
            "title": "Blue",
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
    ]


def cases():
    return [
        {
            "id": "q",
            "dataset": "test",
            "question": "Where was the author of Blue born?",
            "answers": [["Rome"]],
            "answerable": True,
            "support": ["u1", "u2"],
            "documents": ["a", "b"],
            "track": "multi_hop",
            "requirements": [
                {"id": "r1", "text": "Identify the birthplace of the author of Blue."}
            ],
            "stratum": "bridge",
            "difficulty": "two-hop",
        }
    ]


def graph():
    return {
        "facts": [
            {
                "d": "a",
                "s": "Blue",
                "p": "written_by",
                "o": "Ada",
                "n": "positive",
                "q": "Ada wrote Blue.",
                "qualifiers": [],
            },
            {
                "d": "b",
                "s": "Ada",
                "p": "born_in",
                "o": "Rome",
                "n": "positive",
                "q": "Ada was born in Rome.",
                "qualifiers": [],
            },
        ],
        "observations": [],
        "local_edges": [],
        "construction": {},
    }


@pytest.mark.parametrize("model", quality.WORK_MODELS)
async def test_two_method_replay_preserves_dense_order_and_graph_independence(model):
    ranks = quality.rank_bundle(cases(), docs(), [["b", "a"]])
    g = {**graph(), "model": model}
    packets = await run.retrieve_packets(cases(), docs(), g, ranks, model=model)
    by = {p["arm"]: p for p in packets}
    assert set(by) == set(quality.ARMS)
    assert [s["id"] for s in by["vector_rag"]["evidence"]["sources"]] == ["b", "a"]
    assert by["vector_rag"]["evidence"]["paths"] == []
    assert by["domain_kg"]["evidence"]["paths"]
    assert all(p["diagnostics"]["content_chars"] <= 14000 for p in packets)
    others = await run.retrieve_packets(cases(), docs(), {**g, "facts": []}, ranks, model=model)
    assert others[0]["evidence"] == by["vector_rag"]["evidence"]
    assert all("reference" not in p and p["model"] == model for p in packets)


async def test_different_models_cannot_share_a_constructed_graph():
    ranks = quality.rank_bundle(cases(), docs(), [["a", "b"]])
    with pytest.raises(ValueError, match="model"):
        await run.retrieve_packets(
            cases(),
            docs(),
            {**graph(), "model": quality.WORK_MODELS[0]},
            ranks,
            model=quality.WORK_MODELS[1],
        )


def test_index_preserves_explicit_isolates_instead_of_deriving_nodes_only_from_edges():
    g = {**graph(), "observations": [{"id": "node", "d": "a", "name": "Isolated", "type": "name"}]}
    documents = docs()
    documents[0]["text"] += " Isolated is another name."
    index = build_index(documents, g)
    assert any(n.name == "Isolated" for n in index.nodes.values())


def test_changed_frozen_input_is_rejected_before_any_provider_call(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(cases()))
    run.seal(tmp_path, ["cases.json"])
    run.verify(tmp_path)
    path.write_text("[]")
    with pytest.raises(ValueError, match="frozen"):
        run.verify(tmp_path)


def test_calibration_exercises_real_prompt_parser_and_does_not_leak_expected_labels():
    fixtures = run.calibration_cases()
    assert len(fixtures) >= 8
    jobs = run.calibration_jobs(fixtures)
    for job in jobs:
        payload = json.loads(job["request"]["messages"][1]["content"])
        assert "expected" not in payload
    # No model calls yet => no pass merely because fixture schema is valid.
    result = run.calibration_report(fixtures, {})
    assert not result["passed"]
    assert result["unknown"] == len(fixtures)


def test_empty_judgment_store_produces_unknowns_for_every_planned_arm():
    packets = [
        {
            **cases()[0],
            "arm": arm,
            "model": quality.WORK_MODELS[0],
            "evidence": {"sources": [], "paths": []},
            "reference_sources": [],
            "reference": {},
        }
        for arm in quality.ARMS
    ]
    result = run.score_packets(packets, {})
    assert len(result) == 2
    assert all(r["scores"]["grounded_correct"] is None for r in result)
    assert all("reader_missing" in r["errors"] for r in result)


async def test_reader_and_judge_results_are_keyed_by_full_request_not_case_id(tmp_path):
    packet = {
        **cases()[0],
        "arm": "vector_rag",
        "model": quality.WORK_MODELS[0],
        "evidence": {"sources": [], "paths": []},
        "reference_sources": [],
    }
    old = run.jobs_for("answer", [packet], {})[0]
    packet["question"] = "A different question?"
    new = run.jobs_for("answer", [packet], {})[0]
    assert old["key"] != new["key"]


async def test_semantic_at_k_and_missing_judges_stay_unknown():
    from eval.source_role_eval import digest

    model = quality.WORK_MODELS[0]
    packets = await run.retrieve_packets(
        cases(),
        docs(),
        {**graph(), "model": model},
        quality.rank_bundle(cases(), docs(), [["a", "b"]]),
        model=model,
    )
    results = {}
    for p in packets:
        raw = '{"answer":"Rome","citations":["a","b"],"abstain":false}'
        reader = quality.parse_reader(raw, {"a", "b"})
        results[digest(quality.reader_request(p))] = {"content": raw, "error": None}
        for k in quality.KS:
            candidate = run.evidence_at_k(p, k)
            n = len(candidate["evidence"]["sources"])
            value = {
                "answer": "Rome",
                "citations": [s["id"] for s in candidate["evidence"]["sources"]],
                "sources": [
                    {"id": s["id"], "relevance": "useful"} for s in candidate["evidence"]["sources"]
                ],
                "requirements": [{"id": "r1", "coverage": "covered" if n == 2 else "partial"}],
                "reason": "Source-based fixture.",
            }
            results[digest(quality.evidence_request(candidate))] = {
                "content": json.dumps(value),
                "error": None,
            }
        if p["arm"] == "domain_kg":
            continue
        value = {
            "correctness": "correct",
            "citations": "supported",
            "reference_status": "consistent",
            "path_status": "none",
            "requirements": [{"id": "r1", "coverage": "covered"}],
            "claims": [{"claim": "Rome is the requested birthplace.", "grounding": "supported"}],
            "reason": "The bridge entails Rome.",
        }
        results[digest(quality.qa_request(p, reader))] = {
            "content": json.dumps(value),
            "error": None,
        }
    rows = run.score_packets(packets, results)
    group = quality.summarize(cases(), rows)["models"][model]["groups"]["all"]
    assert group["arms"]["vector_rag"]["grounded_correct"]["mean_known"] == 1
    assert group["arms"]["domain_kg"]["grounded_correct"]["unknown"] == 1
    assert (
        group["arms"]["vector_rag"]["retrieval_at_k"]["1"]["semantic_coverage"]["mean_known"] == 0.5
    )
    assert (
        group["arms"]["vector_rag"]["retrieval_at_k"]["12"]["evidence_sufficient"]["mean_known"]
        == 1
    )
    assert group["paired"]["grounded_correct"]["losses"] == 0
    assert len(run.jobs_for("evidence", packets, {})) == 2  # dedup equal evidence, including k > n


def test_cached_rank_import_rejects_different_embedding_profile(tmp_path):
    original, target = tmp_path / "old", tmp_path / "new"
    for root in (original, target):
        root.mkdir()
        (root / "cases.json").write_text(json.dumps(cases()))
        (root / "corpus.json").write_text(json.dumps(docs()))
    (original / "vector-ranks.json").write_text('[["a","b"]]')
    definition = {"profile": {"embedding_model": "different-model", "embedding_dimensions": 1024}}
    (original / "definition.json").write_text(json.dumps(definition))
    with pytest.raises(ValueError, match="embedding profile"):
        run.import_ranks(target, original)
    assert not (target / "ranks.json").exists()


def test_frozen_model_selection_excludes_unavailable_models_without_changing_metrics(tmp_path):
    model = quality.WORK_MODELS[-1]
    run.save_once(tmp_path / "protocol.json", {**quality.PROTOCOL, "working_models": [model]})
    assert run.selected_models(tmp_path) == (model,)
    result = quality.summarize(cases(), [], models=(model,))
    assert set(result["models"]) == {model}
    assert (
        result["models"][model]["groups"]["all"]["arms"]["domain_kg"]["grounded_correct"]["unknown"]
        == 1
    )


def test_judge_selection_is_explicit_and_shared_by_all_assessment_phases(tmp_path):
    judge = "qwen3.6-35b-a3b"
    run.save_once(tmp_path / "protocol.json", {**quality.PROTOCOL, "judge": judge})
    assert run.selected_judge(tmp_path) == judge
    fixtures = run.calibration_cases()
    jobs = run.calibration_jobs(fixtures, judge=judge)
    assert {j["request"]["model"] for j in jobs} == {judge}
    for fixture in fixtures:
        if fixture["kind"] == "evidence":
            default = quality.evidence_request(fixture["packet"])
            selected = quality.evidence_request(fixture["packet"], judge=judge)
        else:
            default = quality.qa_request(fixture["packet"], fixture["reader"])
            selected = quality.qa_request(fixture["packet"], fixture["reader"], judge=judge)
        assert default["model"] == quality.JUDGE
        assert selected == {**default, "model": judge}
    assert quality.PROTOCOL["judge"] == quality.JUDGE  # No mutable global override.


def test_old_judge_scores_cannot_satisfy_new_judge_calibration():
    fixtures = run.calibration_cases()
    old_results = {
        j["key"]: {"content": "{}", "error": None} for j in run.calibration_jobs(fixtures)
    }
    result = run.calibration_report(fixtures, old_results, judge="qwen3.6-35b-a3b")
    assert result["unknown"] == len(fixtures)
    assert not result["passed"]


def test_unknown_frozen_judge_is_rejected(tmp_path):
    run.save_once(tmp_path / "protocol.json", {"judge": "unconfigured-model"})
    with pytest.raises(ValueError, match="judge"):
        run.selected_judge(tmp_path)


def test_reference_isolated_support_request_never_exposes_gold():
    fixture = next(f for f in run.calibration_cases() if f["id"] == "correct-guess-unsupported")
    judge = "qwen3.6-35b-a3b"
    support = quality.grounding_request(fixture["packet"], fixture["reader"], judge=judge)
    data = json.loads(support["messages"][1]["content"])
    assert set(data) == {"question", "candidate_evidence", "reader"}
    assert data["candidate_evidence"] == fixture["packet"]["evidence"]
    assert "gold" not in data["candidate_evidence"]["sources"][0]["id"]
    correct = quality.correctness_request(fixture["packet"], fixture["reader"], judge=judge)
    assert "reference_sources" in json.loads(correct["messages"][1]["content"])
    assert support["model"] == correct["model"] == judge


def test_reference_correct_answer_does_not_become_grounded_without_candidate_support():
    fixture = next(f for f in run.calibration_cases() if f["id"] == "correct-guess-unsupported")
    correct = {
        "correctness": "correct",
        "reference_status": "consistent",
        "requirements": [{"id": "r1", "coverage": "covered"}],
        "reason": "The answer is factually correct.",
    }
    support = {
        "citations": "unsupported",
        "path_status": "none",
        "claims": [{"claim": "Paris is the capital of France.", "grounding": "unsupported"}],
        "reason": "The supplied passage only discusses Ada and Blue.",
    }
    assessed = quality.parse_separate_qa(
        json.dumps(correct), json.dumps(support), fixture["packet"], fixture["reader"]
    )
    scored = quality.score(fixture["reader"], None, assessed)
    assert scored["semantic_correct"] == 1
    assert scored["grounded_correct"] == 0
    assert scored["unsupported_claim_rate"] == 1
    assert assessed["reason"] == correct["reason"]
    assert assessed["support_reason"] == support["reason"]


def test_separate_judging_preserves_each_component_and_keeps_citation_error_separate():
    fixture = next(f for f in run.calibration_cases() if f["id"] == "cited-irrelevant-source")
    judge = "qwen3.6-35b-a3b"
    jobs = run.qa_jobs(fixture["packet"], fixture["reader"], judge=judge, separate_grounding=True)
    assert {j["request"]["model"] for j in jobs} == {judge}
    assert {j["kind"] for j in jobs} == {"qa_correctness", "qa_support"}
    correct = {
        "correctness": "correct",
        "reference_status": "consistent",
        "requirements": [{"id": "r1", "coverage": "covered"}],
        "reason": "Rome is correct.",
    }
    support = {
        "citations": "unsupported",
        "path_status": "none",
        "claims": [{"claim": "Ada was born in Rome.", "grounding": "supported"}],
        "reason": "Source s2 supports the answer, but the reader cited s1.",
    }
    results = {jobs[0]["key"]: {"error": None, "content": json.dumps(correct)}}
    partial, error = run.qa_result(
        fixture["packet"], fixture["reader"], results, judge=judge, separate_grounding=True
    )
    assert error and quality.score(fixture["reader"], None, partial)["semantic_correct"] == 1
    assert quality.score(fixture["reader"], None, partial)["grounded_correct"] is None
    results[jobs[1]["key"]] = {"error": None, "content": json.dumps(support)}
    assessed, error = run.qa_result(
        fixture["packet"], fixture["reader"], results, judge=judge, separate_grounding=True
    )
    assert error is None
    scores = quality.score(fixture["reader"], None, assessed)
    assert scores["grounded_correct"] == 1
    assert scores["citation_supported"] == 0
    assert scores["unsupported_claim_rate"] == 0


def test_extraction_omissions_do_not_block_other_models_but_judge_still_must_pass(
    tmp_path, monkeypatch
):
    run.save_once(
        tmp_path / "preflight" / "summary.json",
        {"passed": False, "embedding": "passed", "models": {}},
    )
    monkeypatch.setattr(run, "calibration_report", lambda *_, **__: {"passed": True})
    run.require_gates(tmp_path)
    monkeypatch.setattr(run, "calibration_report", lambda *_, **__: {"passed": False})
    with pytest.raises(ValueError, match="calibration"):
        run.require_gates(tmp_path)


async def test_full_six_by_two_report_preserves_all_planned_unknowns(tmp_path):
    packets = []
    for model in quality.WORK_MODELS:
        g = {**graph(), "model": model}
        packets.extend(
            await run.retrieve_packets(
                cases(), docs(), g, quality.rank_bundle(cases(), docs(), [["a", "b"]]), model=model
            )
        )
        run.save_once(
            tmp_path / "construction" / model / "graph-statistics.json",
            run.graph_statistics(docs(), g),
        )
    run.save_once(tmp_path / "cases.json", cases())
    run.save_once(tmp_path / "packets.json", packets)
    run.save_once(tmp_path / "provenance.json", {"scope": "synthetic test fixture"})
    summary = run.report(tmp_path)
    assert len(packets) == 12
    assert not summary["semantic_ready"]
    assert summary["performance"]["answer_and_judge"]["attempts"] == 0
    assert "support_recall" not in (tmp_path / "report.md").read_text()
    assert len(run.read(tmp_path / "scored-rows.json")) == 12
    for model in quality.WORK_MODELS:
        group = summary["models"][model]["groups"]["all"]
        for arm in quality.ARMS:
            assert group["arms"][arm]["evidence_sufficient"]["known"] == 0
            assert group["arms"][arm]["grounded_correct"]["unknown"] == 1


def test_path_only_diagnostic_error_preserves_answer_metrics():
    fixture = next(f for f in run.calibration_cases() if f["id"] == "correct-guess-unsupported")
    packet = {**fixture["packet"], "evidence": {**fixture["packet"]["evidence"], "paths": [[{}]]}}
    correct = {
        "correctness": "correct",
        "reference_status": "consistent",
        "requirements": [{"id": "r1", "coverage": "covered"}],
        "reason": "Correct.",
    }
    support = {
        "citations": "unsupported",
        "path_status": "none",
        "claims": [{"claim": "Paris.", "grounding": "unsupported"}],
        "reason": "Absent.",
    }
    assessed = quality.parse_separate_qa(
        json.dumps(correct), json.dumps(support), packet, fixture["reader"]
    )
    assert assessed["path_status"] == "uncertain"
    assert assessed["path_error"] == "inconsistent_path_status"
    scores = quality.score(fixture["reader"], None, assessed)
    assert scores["semantic_correct"] == 1 and scores["grounded_correct"] == 0


def test_bounded_review_is_reference_isolated_and_disagreement_is_unknown():
    fixture = next(f for f in run.calibration_cases() if f["id"] == "correct-guess-unsupported")
    base = quality.grounding_request(fixture["packet"], fixture["reader"])
    review = quality.review_request(base)
    assert review["messages"][1] == base["messages"][1]
    assert review != base
    left = {"correctness": "correct", "requirements": [{"id": "r1", "coverage": "covered"}]}
    right = {"correctness": "incorrect", "requirements": [{"id": "r1", "coverage": "missing"}]}
    agreed = quality.reviewed_assessment(left, right)
    assert quality.score(fixture["reader"], None, agreed)["semantic_correct"] is None
    assert quality.score(fixture["reader"], None, agreed)["answer_completeness"] is None


async def test_source_only_merge_uses_shared_ranker_and_original_text_for_both_arms(tmp_path):
    from domain_kg.providers.rerank import RANKING_INSTRUCTIONS
    from eval.kg_quality_merge import SourceMerger

    merger = SourceMerger(tmp_path, "not-a-secret")
    requests = []

    async def chat(client, payload):
        requests.append(payload)
        supplied = json.loads(payload["messages"][1]["content"])
        assert payload["messages"][0]["content"] == RANKING_INSTRUCTIONS
        assert {e["text"] for e in supplied["evidence"]} == {
            d["title"] + "\n" + d["text"] for d in docs()
        }
        assert all(set(e) == {"index", "text"} for e in supplied["evidence"])
        return {"error": None, "content": json.dumps({"order": [1, 0]})}

    merger.calls.chat = chat
    model = "qwen3.6-35b-a3b"
    packets = await run.retrieve_packets(
        cases(),
        docs(),
        {**graph(), "model": model},
        quality.rank_bundle(cases(), docs(), [["a", "b"]]),
        model=model,
        merge=merger,
    )
    assert len(requests) == 2 and {p["model"] for p in packets} == {model}
    assert all(p["evidence"]["paths"] == [] for p in packets)
    assert all(p["diagnostics"]["merge"]["candidate_count"] == 2 for p in packets)
    assert [s["id"] for s in packets[0]["evidence"]["sources"]] == ["b", "a"]


def test_support_review_disagreement_does_not_destroy_correctness():
    first = {
        "citations": "supported",
        "grounding": "supported",
        "claims": [{"claim": "A fact.", "grounding": "supported"}],
    }
    second = {
        "citations": "supported",
        "grounding": "unsupported",
        "claims": [{"claim": "Same fact paraphrased.", "grounding": "unsupported"}],
    }
    support = quality.reviewed_assessment(first, second)
    combined = {**support, "correctness": "correct"}
    scores = quality.score(None, None, combined)
    assert scores["semantic_correct"] == 1
    assert scores["grounded_correct"] is None
    assert scores["unsupported_claim_rate"] is None
    assert scores["citation_supported"] == 1


def test_known_support_failure_still_fails_grounded_answer_despite_correctness_disagreement():
    qa = {
        "correctness": "correct",
        "grounding": "partial",
        "claims": [
            {"claim": "One supported fact.", "grounding": "supported"},
            {"claim": "One unsupported fact.", "grounding": "unsupported"},
        ],
        "metric_unknowns": ["semantic_correct", "grounded_correct"],
    }
    scores = quality.score(None, None, qa)
    assert scores["semantic_correct"] is None
    assert scores["grounded_correct"] == 0


async def test_failed_source_merge_does_not_abort_or_score_missing_retrieval_as_empty(tmp_path):
    from eval.kg_quality_merge import SourceMerger

    merger = SourceMerger(tmp_path, "unused")

    async def chat(client, payload):
        return {"error": "ConnectError", "content": None}

    merger.calls.chat = chat
    model = "qwen3.6-35b-a3b"
    packets = await run.retrieve_packets(
        cases(),
        docs(),
        {**graph(), "model": model},
        quality.rank_bundle(cases(), docs(), [["a", "b"]]),
        model=model,
        merge=merger,
    )
    assert len(packets) == 2
    assert all(p["retrieval_error"] == "ConnectError" for p in packets)
    assert run.jobs_for("answer", packets, {}) == []
    assert run.jobs_for("evidence", packets, {}) == []
    scored = run.score_packets(packets, {})
    assert all(r["scores"]["evidence_sufficient"] is None for r in scored)
    assert all(r["scores"]["grounded_correct"] is None for r in scored)
    assert all("retrieval_ConnectError" in r["errors"] for r in scored)


async def test_source_merge_accepts_semantic_subset_and_uses_production_backfill(tmp_path):
    from eval.kg_quality_merge import SourceMerger

    merger = SourceMerger(tmp_path, "unused")

    async def chat(client, payload):
        return {"error": None, "content": '{"order":[1]}'}

    merger.calls.chat = chat
    model = "qwen3.6-35b-a3b"
    packets = await run.retrieve_packets(
        cases(),
        docs(),
        {**graph(), "model": model},
        quality.rank_bundle(cases(), docs(), [["a", "b"]]),
        model=model,
        merge=merger,
    )
    assert all(not p["retrieval_error"] for p in packets)
    assert all([s["id"] for s in p["evidence"]["sources"]] == ["b", "a"] for p in packets)
