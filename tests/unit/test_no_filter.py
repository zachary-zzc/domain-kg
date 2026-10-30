import pytest

from eval.no_filter import contexts


def corpus():
    return [{"id": f"c{i}", "text": str(i) * 300} for i in range(1, 9)]


def test_graph_additions_preserve_all_vector_evidence():
    result = contexts(corpus(), ["c1", "c2", "c3", "c4", "c5", "c6"], ["c8", "c7"], "q")
    assert result["V3"] == ["c1", "c2", "c3"]
    assert result["V6"] == ["c1", "c2", "c3", "c4", "c5", "c6"]
    assert result["U"] == ["c1", "c2", "c3", "c4", "c5", "c6", "c8", "c7"]


def test_duplicate_evidence_does_not_spend_additional_context():
    result = contexts(corpus(), ["c1", "c1", "c2"], ["c2", "c3", "c3"], "q")
    assert result["U"] == ["c1", "c2", "c3"]


def test_oversized_graph_chunk_is_omitted_whole_without_evicting_vector():
    chunks = corpus() + [{"id": "long", "text": "x" * 1200}]
    result = contexts(chunks, ["c1", "c2", "c3", "c4", "c5", "c6"], ["long", "c7"], "q")
    assert result["U"] == ["c1", "c2", "c3", "c4", "c5", "c6", "c7"]
    assert result["omitted_graph"] == ["long"]


def test_unknown_candidates_and_duplicate_corpus_fail_closed():
    with pytest.raises(ValueError):
        contexts(corpus(), ["foreign"], [], "q")
    with pytest.raises(ValueError):
        contexts(corpus() + [corpus()[0]], ["c1"], [], "q")


def test_request_overhead_is_bounded_even_when_evidence_is_small():
    with pytest.raises(ValueError):
        contexts(corpus(), ["c1"], [], "q" * 4096)
