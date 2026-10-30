import importlib
import json

import pytest

from eval.dual_path_retrieval import GraphIndex


def audit():
    try:
        return importlib.import_module("eval.retrieval_audit")
    except ModuleNotFoundError:
        pytest.fail("offline trace and bounded retrieval variants are not implemented")


def documents(names):
    return {n: {"title": n, "text": n, "units": [{"id": n, "title": n, "text": n}]} for n in names}


def test_backfill_uses_unused_vector_slots_without_evicting_existing_graph_or_vector():
    mod = audit()
    v = [f"v{i}" for i in range(12)]
    docs = documents([*v, "g0", "g1"])
    result = mod.fill_context([*v[:6], "g0", "g1"], v, docs)
    assert result == [*v[:6], "g0", "g1", "v6", "v7", "v8", "v9"]


def test_backfill_enforces_serialized_source_budget_and_complete_units():
    mod = audit()
    docs = documents(["a", "b", "c"])
    docs["b"]["units"][0]["text"] = "B" * 2000
    result = mod.fill_context(["a"], ["a", "b", "c"], docs, budget=200)
    assert result == ["a", "c"]
    assert len(json.dumps([docs[x]["units"][0] for x in result], ensure_ascii=False)) <= 200
    assert docs["b"]["units"][0]["text"] == "B" * 2000


def test_foreign_document_cannot_be_silently_filled_or_ranked():
    mod = audit()
    with pytest.raises(ValueError, match="outside corpus"):
        mod.fill_context(["a"], ["foreign"], documents(["a"]))


def test_trace_exposes_hash_order_ties_and_text_breaker_prefers_query_evidence():
    mod = audit()
    facts = [
        {"d": "a", "s": "Team Zerun", "o": "Device Nerak"},
        {"d": "z", "s": "Team Zerun", "o": "Device Palar"},
    ]
    docs = documents(["a", "z"])
    docs["a"].update(title="Catering", text="A catering announcement")
    docs["z"].update(title="Orbit", text="Team Zerun orbit mission")
    trace = mod.trace_graph(GraphIndex(facts), "Team Zerun orbit")
    assert trace["rank"] == ["a", "z"]
    assert trace["scores"]["a"] == trace["scores"]["z"]
    assert trace["top_score_ties"] == 2
    index = mod.SourceBM25(docs)
    assert mod.tie_rank(trace, index, "Team Zerun orbit")[0] == "z"


def test_graph_cap_trace_matches_actual_first_400_edges_and_200_nodes():
    mod = audit()
    facts = [{"d": f"d{i}", "s": "Hub", "o": f"Node{i}"} for i in range(500)]
    result = mod.trace_graph(GraphIndex(facts), "Hub")
    assert result["credited_edges"] == 400
    assert result["visited_nodes"] == 200
    assert result["cap_hit"] is True
    assert result["rank"][:6] == ["d0", "d1", "d10", "d100", "d101", "d102"]


def test_rrf_rewards_corroborated_tail_candidates_and_protects_v6():
    mod = audit()
    v = [f"v{i}" for i in range(12)]
    docs = documents([*v, "g0", "g1"])
    result = mod.pool_context(v, ["g0", "v7", "g1"], docs)
    assert result[:6] == v[:6]
    assert result[6] == "v7"
    assert len(result) <= 12 and len(set(result)) == len(result)


def test_complete_retained_context_cannot_be_mutated_or_overfilled():
    mod = audit()
    retained = [str(i) for i in range(12)]
    docs = documents([*retained, "extra"])
    assert mod.fill_context(retained, ["extra"], docs) == retained
    assert retained == [str(i) for i in range(12)]
