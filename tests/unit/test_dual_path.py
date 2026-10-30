import json

import pytest

from eval import dual_path_data as data
from eval import dual_path_retrieval as retrieval


def case(ident, text="A uses B."):
    return {
        "id": ident,
        "dataset": "test",
        "question": "What is used?",
        "contexts": [{"id": "p0", "title": ident, "text": text}],
        "support": ["p0"],
        "answers": [["B"]],
        "answerable": True,
        "stratum": "single",
        "difficulty": "single",
    }


def test_local_ids_cannot_cross_attribute_and_builder_has_no_gold():
    docs, cases = data.corpus([case("one"), case("two", "C uses D.")])
    assert len(docs) == 2
    assert cases[0]["support"] != cases[1]["support"]
    request = data.extraction_request("qwen-test", docs)
    content = request["messages"][-1]["content"]
    payload = json.loads(content.split("Actual sources:\n")[1].split("\nReturn only")[0])
    assert set(payload) == {"sources"}
    assert all(set(d) == {"id", "title", "text"} for d in payload["sources"])
    assert "question" not in json.dumps(payload)


def test_repeated_passage_has_stable_scoped_identity():
    a, b = case("one"), case("two")
    b["contexts"][0]["title"] = "one"
    docs, cases = data.corpus([a, b])
    assert len(docs) == 1
    assert cases[0]["support"] == cases[1]["support"]


def test_hotpot_paragraph_grouping_preserves_sentence_support():
    c = case("one")
    c.update(
        dataset="hotpot",
        contexts=[
            {"id": "p0s0", "title": "A", "text": "A uses B. "},
            {"id": "p0s1", "title": "A", "text": "B is blue."},
        ],
        support=["p0s1"],
    )
    docs, cases = data.corpus([c])
    assert len(docs) == 1
    assert len(docs[0]["units"]) == 2
    assert cases[0]["support"] == [docs[0]["units"][1]["id"]]


def test_extraction_fence_normalization_and_quote_validation():
    docs, _ = data.corpus([case("one")])
    fact = {"d": docs[0]["id"], "s": "A", "p": "uses", "o": "B", "n": "positive", "q": "A uses B."}
    wire = {**fact, "d": "0"}
    result = data.parse_graph("```json\n" + json.dumps({"facts": [wire]}) + "\n```", docs)
    assert result["facts"] == [fact]
    assert result["raw_json"] is False
    bad = {**wire, "q": "B uses A."}
    result = data.parse_graph(json.dumps({"facts": [bad]}), docs)
    assert result["facts"] == []
    assert result["rejected"] == 1
    assert data.parse_graph('prefix {"facts": []}', docs) is None


def test_source_reference_and_bounded_degree_cannot_be_model_chosen():
    docs, _ = data.corpus([case("one")])
    f = {"d": "other-kg", "s": "A", "p": "uses", "o": "B", "n": "positive", "q": "A uses B."}
    result = data.parse_graph(json.dumps({"facts": [f]}), docs)
    assert result["facts"] == []
    assert result["rejected"] == 1


def test_gold_scores_direction_and_negation_not_just_grounding():
    gold = [{"d": "d", "s": "A", "p": "uses", "o": "B", "n": "negative"}]
    reverse = [{**gold[0], "s": "B", "o": "A"}]
    positive = [{**gold[0], "n": "positive"}]
    assert data.fact_scores(gold, gold)["f1"] == 1
    assert data.fact_scores(gold, reverse)["recall"] == 0
    assert data.fact_scores(gold, positive)["precision"] == 0


def test_fusion_keeps_packed_vector_evidence_under_budget():
    docs, _ = data.corpus([case(str(i), str(i) * 50) for i in range(10)])
    lookup = {d["id"]: d for d in docs}
    v = [d["id"] for d in docs[:6]]
    g = [d["id"] for d in docs[5:]]
    arms = retrieval.contexts(v + g, g, lookup, budget=1000)
    assert set(arms["V6"]) <= set(arms["U12"])
    assert len(arms["V12"]) <= 12
    assert len(set(arms["U12"])) == len(arms["U12"])


def test_graph_recall_is_independent_and_preserves_source_nodes():
    facts = [
        {"d": "d1", "s": "Project Zerun", "p": "operated_by", "o": "Team Nerak", "n": "positive"},
        {"d": "d2", "s": "Team Nerak", "p": "uses", "o": "Device Palar", "n": "positive"},
        {"d": "d3", "s": "Device Palar", "p": "located_in", "o": "Site Romel", "n": "positive"},
    ]
    index = retrieval.GraphIndex(facts)
    found = index.recall("Where is the device used by the operator of Project Zerun stored?")
    assert set(found) == {"d1", "d2", "d3"}
    assert all(f["d"] in {"d1", "d2", "d3"} for f in index.facts)


def test_json_cleanup_does_not_repair_truncation_or_surrounding_prose():
    assert data.json_object('```json\n{"a":1}\n```') == {"a": 1}
    assert data.json_object('intro {"a":1}') is None
    assert data.json_object('{"a":1') is None
    assert data.json_object("```json\n{}\n```\n```json\n{}\n```") is None


def test_pack_batches_does_not_split_sources_or_exceed_limit():
    docs, _ = data.corpus([case(str(i), "x" * 300) for i in range(12)])
    batches = data.batches(docs, max_chars=1000, max_docs=3)
    assert len(batches) == 4
    assert sum(map(len, batches)) == len(docs)
    with pytest.raises(ValueError):
        data.batches(docs, max_chars=100, max_docs=3)


def test_runner_tracks_unknown_cost_and_rejects_duplicate_results(tmp_path):
    from eval.dual_path_run import RunBudget, load_rows
    from eval.public_qa_data import MODELS

    budget = RunBudget()
    budget.add(MODELS[0], None)
    assert budget.reserved == 1
    budget.cost = 99
    assert budget.stopped()
    (tmp_path / "a.json").write_text(json.dumps({"identity": "same"}))
    (tmp_path / "b.json").write_text(json.dumps({"identity": "same"}))
    with pytest.raises(ValueError, match="duplicate"):
        load_rows(tmp_path)


def test_runner_does_not_treat_json_fence_as_semantic_answer_failure():
    from eval.dual_path_run import parse_answer

    c = case("one")
    obj = {"answer": "B", "citations": ["p0"], "abstain": False}
    answer, raw = parse_answer("```json\n" + json.dumps(obj) + "\n```", c)
    assert answer == obj
    assert raw is False


def test_answer_jobs_rejects_graph_source_from_different_corpus():
    from eval.dual_path_run import answer_jobs

    docs, cases = data.corpus([case("one")])
    foreign = {"d": "foreign", "s": "A", "p": "uses", "o": "B", "n": "positive"}
    with pytest.raises(ValueError, match="graph source outside corpus"):
        answer_jobs(cases, docs, [[docs[0]["id"]]], {"model": [foreign]}, ["model"], "pilot_answer")


def test_duplicate_json_keys_are_rejected_instead_of_losing_facts():
    assert data.json_object('{"facts":[],"facts":[1]}') is None


def test_short_wire_source_ids_map_back_to_immutable_global_ids():
    docs, _ = data.corpus([case("one"), case("two", "C uses D.")])
    request = data.extraction_request("model", docs)
    content = request["messages"][-1]["content"]
    payload = json.loads(content.split("Actual sources:\n")[1].split("\nReturn only")[0])
    assert [s["id"] for s in payload["sources"]] == ["0", "1"]
    fact = {"d": "0", "s": "A", "p": "uses", "o": "B", "n": "positive", "q": "A uses B."}
    # Locate A's source independently of corpus sorting.
    source_index = next(i for i, d in enumerate(docs) if d["text"] == "A uses B.")
    fact["d"] = str(source_index)
    parsed = data.parse_graph(json.dumps({"facts": [fact]}), docs)
    assert parsed["facts"][0]["d"] == docs[source_index]["id"]


def test_reader_short_citations_map_back_without_guessing_or_auto_citing():
    from eval.dual_path_run import parse_answer

    docs, cases = data.corpus([case("one")])
    c = cases[0]
    payload = data.answer_request("model", c, c["contexts"])
    assert '"id": "e0"' in payload["messages"][-1]["content"]
    c["wire_ids"] = {"e0": c["contexts"][0]["id"]}
    answer, _ = parse_answer('{"answer":"B","citations":["e0"],"abstain":false}', c)
    assert answer["citations"] == c["support"]
    assert parse_answer('{"answer":"B","citations":["e9"],"abstain":false}', c)[0] is None


def test_full_run_keeps_vectors_and_answer_payloads_out_of_checkpoint_memory(tmp_path):
    from eval.dual_path_run import answer_jobs, load_rows

    (tmp_path / "row.json").write_text(json.dumps({"identity": "a", "vectors": [[1.0, 2.0]]}))
    assert "vectors" not in load_rows(tmp_path)[0]
    docs, cases = data.corpus([case("one")])
    records, jobs = answer_jobs(
        cases, docs, [[docs[0]["id"]]], {"model": []}, ["model"], "formal_answer"
    )
    assert len(records) == len(jobs) == 4
    assert all("payload" not in j for j in jobs)
