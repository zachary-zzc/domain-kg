from eval.source_role_analysis import construction_stats, evidence_stats, paired_intervals


def test_unjudged_facts_are_visible_and_coverage_deduplicates_semantic_reference():
    row = {
        "id": "one",
        "arms": {"baseline": {"facts": [{}, {}], "raw_count": 2, "rejections": []}},
        "fact_mapping": {"a": {"arm": "baseline"}, "b": {"arm": "baseline"}},
    }
    missing = {**row, "id": "two"}
    judgment = {
        "facts": [
            {"id": "a", "verdict": "supported", "matches": ["r"]},
            {"id": "b", "verdict": "supported", "matches": ["r"]},
        ],
        "references": [{"id": "r", "verdict": "supported"}],
    }
    stats = construction_stats([row, missing], {"one": judgment, "two": None})["baseline"]
    assert stats["supported"] == 2 and stats["accepted_facts"] == 4
    assert stats["unjudged_facts"] == 2 and stats["judged_documents"] == 1
    assert stats["covered"] == 1 and stats["reference_facts"] == 1
    intervals = paired_intervals([row], {"one": judgment}, "baseline", "coverage")
    assert intervals["precision"] is None
    assert intervals["coverage"]["second_minus_first"] == -1


def test_source_coverage_accepts_the_shared_evaluators_integer_flag(tmp_path):
    import json

    packet = {
        "id": "q",
        "question": "Question",
        "arms": {"RAG": {"sources": [], "paths": []}},
        "diagnostics": {"RAG": {"reference_coverage": {"complete": 1}}},
    }
    (tmp_path / "packets.json").write_text(json.dumps([packet]))
    stats = evidence_stats(tmp_path)["stats"]["RAG"]
    assert stats["reference_complete"] == 1
    assert stats["valid_judgments"] == 0
