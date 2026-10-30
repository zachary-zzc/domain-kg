from uuid import UUID

import pytest

from domain_kg.graph.views import EdgeView, SubgraphView
from domain_kg.retrieval.models import EvidenceCandidate, GraphStep
from domain_kg.retrieval.ranking import BM25, rank_graph_candidates, rank_seeds, trace_paths

KG = UUID(int=10)


def candidate(identifier, text, paths=()):
    return EvidenceCandidate(
        kind="source",
        kg_id=KG,
        material_id=UUID(int=11),
        version_id=UUID(int=12),
        chunk_id=UUID(int=identifier),
        text=text,
        citation_ids=(UUID(int=identifier + 100),),
        graph_paths=paths,
    )


def edge(identifier, subject, target, predicate="born_in", polarity="positive"):
    return EdgeView(
        subject_id=UUID(int=subject),
        target_id=UUID(int=target),
        assertion_id=UUID(int=identifier),
        support_id=UUID(int=identifier + 100),
        predicate=predicate,
        polarity=polarity,
        confidence=0.9,
        source=None,
        spans=(),
    )


def graph(*edges):
    return SubgraphView(kg_id=KG, revision=1, nodes=(), edges=edges)


def test_graph_relevance_precedes_uuid_and_updates_branch_ranks():
    relevant = candidate(90, "Mira was born in Oslo.")
    unrelated = candidate(1, "Mira owns a bicycle.")
    ranked = rank_graph_candidates("Where was Mira born?", [unrelated, relevant], graph())
    assert ranked[0].chunk_id == relevant.chunk_id
    assert [x.method_ranks["graph"] for x in ranked] == [1, 2]
    assert ranked[0].text == relevant.text


def test_relation_cue_prioritizes_supported_path_without_dropping_other_evidence():
    born, uses = edge(21, 1, 2), edge(22, 1, 3, "uses")
    paths = trace_paths(graph(born, uses), (UUID(int=1),), 2)
    a = candidate(1, "Mira and Oslo.", (paths[uses.support_id],))
    b = candidate(90, "Mira and Oslo.", (paths[born.support_id],))
    result = rank_graph_candidates("Where was Mira born?", [a, b], graph(born, uses))
    assert result[0].chunk_id == b.chunk_id
    assert len(result) == 2


def test_seed_matching_handles_question_phrase_and_ignores_generic_exact_matches():
    labels = {
        UUID(int=1): ("city",),
        UUID(int=2): ("a",),
        UUID(int=90): ("Charles Quinton Murphy", "Charlie Murphy"),
    }
    seeds = rank_seeds("In which city was Charlie Murphy born?", labels)
    assert seeds == (UUID(int=90),)
    assert rank_seeds("HIDDEN_ALIAS", labels) == ()


def test_bm25_scores_only_supplied_corpus_and_normalizes_unicode():
    scorer = BM25({"one": "Ａlpha 蛋白质", "two": "unrelated"})
    assert scorer.scores("Alpha")["one"] > 0
    assert scorer.scores("蛋白质")["one"] > 0
    assert scorer.scores("missing") == {}
    with pytest.raises(ValueError):
        scorer.scores(" ".join(f"term{i}" for i in range(513)))


def test_paths_preserve_negation_direction_and_hop_limits():
    first, second = edge(21, 1, 2, "created_by"), edge(22, 2, 3, polarity="negative")
    g = graph(first, second)
    paths = trace_paths(g, (UUID(int=1),), 2)
    assert len(paths[second.support_id]) == 2
    # Traversal may follow either direction but must not reverse the underlying assertion.
    reverse = trace_paths(g, (UUID(int=3),), 1)[second.support_id][0]
    assert (reverse.subject_id, reverse.target_id) == (UUID(int=2), UUID(int=3))
    assert reverse.predicate == "born_in" and reverse.polarity == "negative"
    assert second.support_id not in trace_paths(g, (UUID(int=1),), 1)
    assert trace_paths(g, (UUID(int=1),), 0) == {}
    assert all(isinstance(s, GraphStep) for s in paths[second.support_id])


def test_graph_ranking_rejects_cross_kg_candidates():
    other = candidate(1, "Mira").model_copy(update={"kg_id": UUID(int=999)})
    with pytest.raises(ValueError, match="KG"):
        rank_graph_candidates("Mira", [other], graph())


async def test_graph_query_budget_is_a_domain_error_before_store_access():
    from types import SimpleNamespace

    from domain_kg.errors import DomainError, ErrorCode
    from domain_kg.retrieval.graph import GraphRetrievalBranch
    from domain_kg.retrieval.models import SearchRequest

    request = SearchRequest(
        kg_ids=(KG,), query=" ".join(f"term{i}" for i in range(513)), mode="graph"
    )
    scope = SimpleNamespace(view=SimpleNamespace(kg_id=KG), filters=request.filters)
    branch = object.__new__(GraphRetrievalBranch)
    with pytest.raises(DomainError) as error:
        await branch.recall(scope, request)
    assert error.value.code == ErrorCode.QUOTA_EXCEEDED
