from types import SimpleNamespace

from eval.kg_quality_graph import topology


def test_structure_counts_isolates_components_duplicates_loops_and_dangling_edges():
    edges = [
        SimpleNamespace(subject_id=a, target_id=b)
        for a, b in [("a", "b"), ("a", "b"), ("b", "b"), ("x", "y")]
    ]
    result = topology({"a", "b", "c"}, edges)
    assert result["nodes"] == 3
    assert result["isolates"] == 1
    assert result["weak_components"] == 2
    assert result["largest_component_nodes"] == 2
    assert result["dangling_edges"] == 1
    assert result["self_loops"] == 1
    assert result["unique_directed_pairs_without_loops"] == 1
    assert result["directed_density"] == 1 / 6
    assert result["neighbor_degree_histogram"] == {0: 1, 1: 2}


def test_empty_graph_does_not_claim_perfect_connectivity():
    result = topology(set(), [])
    assert result["isolates"] == 0
    assert result["isolate_rate"] is None
    assert result["largest_component_fraction"] is None
