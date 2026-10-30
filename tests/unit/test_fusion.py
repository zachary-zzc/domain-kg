from uuid import UUID, uuid4

import pytest

from domain_kg.errors import DomainError
from domain_kg.retrieval.fusion import fuse, fuse_evidence, select_evidence
from domain_kg.retrieval.models import EvidenceCandidate


def evidence(kg=None, chunk=None, text="evidence", score=0.0):
    return EvidenceCandidate(
        kind="source",
        kg_id=kg or uuid4(),
        material_id=UUID(int=1),
        version_id=UUID(int=2),
        chunk_id=chunk or uuid4(),
        text=text,
        citation_ids=(UUID(int=3),),
        score=score,
    )


def test_rrf_ignores_incomparable_cosines_and_duplicate_branch_hits():
    first, second = evidence(score=0.99), evidence(score=0.001)
    result = fuse({"vector": [first, first, second], "lexical": [second]})
    assert result[0].chunk_id == second.chunk_id
    assert result[0].score == pytest.approx(2 / 61)
    assert result[1].score == pytest.approx(1 / 61)
    assert result[0].method_ranks == {"vector": 1, "lexical": 1}


def test_same_chunk_id_in_two_kgs_stays_distinct_and_later_kg_is_not_starved():
    kg1, kg2, chunk = uuid4(), uuid4(), uuid4()
    first = [evidence(kg1, chunk if i == 0 else None) for i in range(5)]
    later = evidence(kg2, chunk)
    fused = fuse({"vector": [*first, later], "lexical": first})
    selected, truncated = select_evidence(fused, top_k=2, max_chars=1000)
    assert {item.kg_id for item in selected} == {kg1, kg2}
    assert truncated and len(fused) == 6


def test_fusion_rejects_conflicting_sealed_text_and_obeys_text_budget():
    item = evidence(text="x" * 1200)
    with pytest.raises(DomainError, match="inconsistent"):
        fuse({"vector": [item], "lexical": [item.model_copy(update={"text": "changed"})]})
    selected, truncated = select_evidence([item], top_k=10, max_chars=1000)
    assert selected[0].text == "x" * 1000 and truncated
    assert selected[0].citation_ids == item.citation_ids


def test_graph_annotations_are_unioned_without_double_counting_a_method():
    item = evidence()
    entity = uuid4()
    result = fuse({"vector": [item], "graph": [item.model_copy(update={"entity_ids": (entity,)})]})
    assert result[0].entity_ids == (entity,)
    assert result[0].score == pytest.approx(2 / 61)


def test_packing_preserves_whole_passages_instead_of_equal_slicing():
    kg = uuid4()
    first = evidence(kg, text="a" * 700)
    second = evidence(kg, text="b" * 100)
    selected, truncated = select_evidence([first, second], top_k=2, max_chars=800)
    assert [item.text for item in selected] == [first.text, second.text]
    assert not truncated


def test_packing_backfills_after_oversized_hit_and_deduplicates():
    kg = uuid4()
    large, first, second = (
        evidence(kg, text="x" * 1500),
        evidence(kg, text="a" * 450),
        evidence(kg, text="b" * 450),
    )
    selected, truncated = select_evidence([large, first, first, second], top_k=2, max_chars=1000)
    assert [item.chunk_id for item in selected] == [first.chunk_id, second.chunk_id]
    assert all(len(item.text) == 450 for item in selected)
    assert truncated


def test_packing_keeps_best_fitting_evidence_for_each_kg():
    kg1, kg2 = uuid4(), uuid4()
    big = evidence(kg1, text="x" * 1001)
    small = evidence(kg1, text="y" * 400)
    later = evidence(kg2, text="z" * 500)
    selected, _ = select_evidence([big, small, later], top_k=2, max_chars=1000)
    assert {item.chunk_id for item in selected} == {small.chunk_id, later.chunk_id}


def test_packing_returns_explicit_excerpt_when_top_k_cannot_represent_all_kgs():
    first, second = evidence(text="a" * 1500), evidence(text="b" * 1500)
    selected, truncated = select_evidence([first, second], top_k=1, max_chars=1000)
    assert truncated and len(selected) == 1
    assert selected[0].chunk_id == first.chunk_id
    assert selected[0].text == first.text[:1000]
    assert selected[0].graph_paths == ()


def test_graph_duplicate_is_annotation_not_an_independent_third_vote():
    kg = uuid4()
    strongest, weaker = evidence(kg), evidence(kg)
    branches = {"vector": [strongest, weaker], "lexical": [strongest, weaker], "graph": [weaker]}
    assert fuse(branches)[0].chunk_id == weaker.chunk_id  # reproduces correlated vote inflation
    result = fuse_evidence(branches)
    assert result[0].chunk_id == strongest.chunk_id
    assert result[1].score == pytest.approx(2 / 62)
    assert result[1].method_ranks["graph"] == 1


def test_graph_can_supply_new_evidence_ahead_of_weak_single_branch_tail():
    kg = uuid4()
    baseline = [evidence(kg) for _ in range(15)]
    novel = evidence(kg)
    result = fuse_evidence({"vector": baseline, "lexical": baseline[:2], "graph": [novel]})
    assert result[0].chunk_id == baseline[0].chunk_id
    assert result.index(next(x for x in result if x.chunk_id == novel.chunk_id)) < 4


def test_graph_supplement_preserves_consistency_checks_and_standalone_mode():
    item = evidence()
    with pytest.raises(DomainError):
        fuse_evidence(
            {
                "vector": [item],
                "graph": [item.model_copy(update={"text": "forged"})],
            }
        )
    assert fuse_evidence({"graph": [item]}) == fuse({"graph": [item]})


def test_opt_in_reranking_can_promote_graph_only_evidence():
    kg = uuid4()
    text, graph_only = evidence(kg), evidence(kg)
    result = fuse_evidence(
        {"vector": [text], "graph": [graph_only], "rerank": [graph_only, text]}
    )
    assert result[0].chunk_id == graph_only.chunk_id
    assert result[0].score == pytest.approx(2 / 61)
