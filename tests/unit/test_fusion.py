from uuid import UUID, uuid4

import pytest

from domain_kg.errors import DomainError
from domain_kg.retrieval.fusion import fuse, select_evidence
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
