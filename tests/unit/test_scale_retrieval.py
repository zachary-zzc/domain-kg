import json
from uuid import NAMESPACE_URL, uuid5

import pytest

from eval import scale_retrieval as scale
from eval.scoped_retrieval import OfflineIndex


async def test_rag_independence_and_total_graph_budget():
    docs = [
        {"id": "a", "dataset": "test", "title": "Alpha", "text": "Alpha uses Beta."},
        {"id": "b", "dataset": "test", "title": "Beta", "text": "Beta is part of Gamma."},
    ]
    facts = [
        {"d": "a", "s": "Alpha", "p": "uses", "o": "Beta", "n": "positive", "q": "Alpha uses Beta."}
    ]
    outputs = []
    for graph in ([], facts):
        index = OfflineIndex(docs, graph, kg_id=uuid5(NAMESPACE_URL, "scale-test"))
        branches, view = await index.retrieve("What does Alpha use?", ["a", "b"])
        rag, _ = scale.pack(index, branches, view, graph=False, max_chars=80)
        deep, _ = scale.pack(index, branches, view, graph=True, max_chars=80)
        assert scale.context_size(deep) <= 80
        assert scale.context_size(rag) <= 80
        outputs.append(rag)
    assert outputs[0] == outputs[1]


def test_reader_and_blind_judge_never_see_reference_answers():
    packet = {
        "question": "Where?",
        "answers": [["SECRET-GOLD"]],
        "reference_sources": [{"text": "SECRET-GOLD"}],
        "evidence": {"sources": [{"id": "a", "text": "Useful evidence"}], "paths": []},
    }
    for payload in (scale.reader_request(packet), scale.blind_request(packet, {"answer": "Here"})):
        assert "SECRET-GOLD" not in json.dumps(payload)
    aware = scale.semantic_request(packet, {"answer": "Here"}, {"sufficiency": "sufficient"})
    assert "SECRET-GOLD" in json.dumps(aware)
    assert "exact match" in aware["messages"][0]["content"]


def test_semantic_judge_accepts_supported_alternative_and_flags_reference_defect():
    raw = json.dumps(
        {
            "semantic": "correct",
            "reference_status": "alternative_supported",
            "reason": "Same person under a different alias; alternate evidence establishes it.",
        }
    )
    assert scale.parse_semantic(raw)["semantic"] == "correct"


def test_citation_protocol_error_does_not_erase_reader_answer():
    raw = json.dumps({"answer": "Copenhagen", "citations": ["invented"], "abstain": False})
    result = scale.parse_reader(raw, {"known"})
    assert result["answer"] == "Copenhagen"
    assert not result["protocol_valid"]
    assert result["invalid_citations"] == ["invented"]


def test_sufficient_judgment_needs_real_candidate_citations():
    value = {
        "relevance": "useful",
        "sufficiency": "sufficient",
        "answer": "A",
        "citations": ["invented"],
        "reader_support": "supported",
        "citation_support": "supported",
        "question_status": "clear",
        "path_status": "none",
        "reason": "Stated.",
    }
    with pytest.raises(ValueError):
        scale.parse_blind(json.dumps(value), {"known"}, False)
