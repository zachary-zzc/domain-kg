from uuid import UUID

import pytest

from domain_kg.retrieval.fusion import fuse_evidence, select_evidence
from eval.scoped_retrieval import OfflineIndex, evidence_coverage, path_context


def documents():
    return [
        {
            "id": "work",
            "dataset": "fixture",
            "title": "Aster",
            "text": "Aster was written by Mira.",
            "units": [{"id": "s1", "text": "Aster was written by Mira."}],
        },
        {
            "id": "birth",
            "dataset": "fixture",
            "title": "Mira",
            "text": "Mira was born in Oslo.",
            "units": [{"id": "s2", "text": "Mira was born in Oslo."}],
        },
        {
            "id": "noise",
            "dataset": "fixture",
            "title": "Aster flowers",
            "text": "Aster flowers grow in gardens.",
            "units": [{"id": "s3", "text": "Aster flowers grow in gardens."}],
        },
    ]


def facts():
    return [
        {
            "d": "work",
            "s": "Aster",
            "p": "created_by",
            "o": "Mira",
            "n": "positive",
            "q": "Aster was written by Mira.",
        },
        {
            "d": "birth",
            "s": "Mira",
            "p": "born_in",
            "o": "Oslo",
            "n": "positive",
            "q": "Mira was born in Oslo.",
        },
    ]


async def test_offline_adapter_shares_native_traversal_ranking_and_packing(monkeypatch):
    from domain_kg.graph.repository import GraphRepository

    original = GraphRepository.traverse
    calls = []

    async def tracked(*args, **kwargs):
        calls.append(True)
        return await original(*args, **kwargs)

    monkeypatch.setattr(GraphRepository, "traverse", tracked)
    index = OfflineIndex(documents(), facts(), kg_id=UUID(int=10))
    branches, view = await index.retrieve("Where was the author of Aster born?", ["work", "noise"])
    assert calls
    assert any(index.document_id(item) == "birth" for item in branches["graph"])
    selected, _ = select_evidence(fuse_evidence(branches), top_k=3, max_chars=1000)
    rendered = path_context(index, selected, view)
    assert rendered["sources"] == [index.document_id(item) for item in selected]
    assert rendered["paths"]
    assert all(
        step["quote"] in index.documents[step["document"]]["text"]
        for path in rendered["paths"]
        for step in path
    )
    assert evidence_coverage({"support": ["s1", "s2"]}, selected, index)["complete"] == 1


async def test_path_presentation_never_uses_unselected_or_cut_off_support():
    index = OfflineIndex(documents(), facts(), kg_id=UUID(int=10))
    branches, view = await index.retrieve("Where was the author of Aster born?", ["work"])
    birth = next(item for item in branches["graph"] if index.document_id(item) == "birth")
    only_birth = birth.model_copy(update={"text": "Mira"})
    assert path_context(index, (only_birth,), view)["paths"] == []
    assert evidence_coverage({"support": ["s2"]}, (only_birth,), index)["complete"] == 0


def test_adapter_scopes_identical_labels_and_rejects_ungrounded_facts():
    first = OfflineIndex(documents(), facts(), kg_id=UUID(int=10))
    second = OfflineIndex(documents(), facts(), kg_id=UUID(int=20))
    assert not set(first.nodes).intersection(second.nodes)
    bad = [{**facts()[0], "q": "fabricated source"}]
    with pytest.raises(ValueError, match="ground"):
        OfflineIndex(documents(), bad, kg_id=UUID(int=10))


async def test_constrained_graph_requires_query_anchor_and_matching_terminal_relation():
    from eval.scoped_retrieval import constrain_graph

    index = OfflineIndex(documents(), facts(), kg_id=UUID(int=10))
    query = "Where was the author of Aster born?"
    branches, view = await index.retrieve(query, ["work", "birth", "noise"])
    constrained = constrain_graph(query, branches["graph"], view)
    birth = next(item for item in constrained if index.document_id(item) == "birth")
    # Vector seeding must not erase the explicit Aster -> author -> birthplace path.
    assert any(
        [step.predicate for step in path] == ["created_by", "born_in"] for path in birth.graph_paths
    )
    assert constrain_graph("Where was an unknown scientist born?", branches["graph"], view) == []
    assert constrain_graph("When was Aster released?", branches["graph"], view) == []


async def test_constrained_paths_keep_direction_negation_and_no_cue_abstention():
    from eval.scoped_retrieval import constrain_graph

    negative = [facts()[0], {**facts()[1], "n": "negative"}]
    index = OfflineIndex(documents(), negative, kg_id=UUID(int=10))
    branches, view = await index.retrieve("Where was the author of Aster born?", ["work"])
    constrained = constrain_graph("Where was the author of Aster born?", branches["graph"], view)
    path = next(path for item in constrained for path in item.graph_paths if len(path) == 2)
    assert path[-1].predicate == "born_in" and path[-1].polarity == "negative"
    assert path[0].target_id == path[-1].subject_id
    assert constrain_graph("Tell me about Aster", branches["graph"], view) == []


async def test_many_vector_seeds_leave_room_for_unseeded_two_hop_evidence():
    # Full node-budget seeding previously removed the bridge and target entirely.
    docs = documents()
    graph = facts()
    for i in range(50):
        text = " ".join(f"N{i}_{j} uses N{i}_{j + 1}." for j in range(5))
        docs.append(
            {"id": f"distractor-{i}", "dataset": "fixture", "title": f"Noise {i}", "text": text}
        )
        graph.extend(
            {
                "d": f"distractor-{i}",
                "s": f"N{i}_{j}",
                "p": "uses",
                "o": f"N{i}_{j + 1}",
                "n": "positive",
                "q": f"N{i}_{j} uses N{i}_{j + 1}.",
            }
            for j in range(5)
        )
    index = OfflineIndex(docs, graph, kg_id=UUID(int=10))
    # The named anchor is found from the question; neither bridge source is vector-seeded.
    branches, view = await index.retrieve(
        "Where was the author of Aster born?", [f"distractor-{i}" for i in range(50)]
    )
    birth = next((c for c in branches["graph"] if index.document_id(c) == "birth"), None)
    assert birth is not None
    assert any(len(path) == 2 for path in birth.graph_paths)
    assert len(view.nodes) <= 200 and len(view.edges) <= 400
    _, zero = await index.retrieve(
        "Where was the author of Aster born?",
        [f"distractor-{i}" for i in range(50)],
        max_hops=0,
    )
    assert len(zero.nodes) == 200 and not zero.edges
