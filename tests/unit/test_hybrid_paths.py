"""A graph augmentation must actually expose a bounded connecting path."""

import importlib

import pytest


def api():
    return importlib.import_module("eval.hybrid_paths")


def corpus():
    return [
        {"id": "x1", "document": "1", "text": "Project Atlas uses the Delta-8 assay."},
        {"id": "x2", "document": "2", "text": "The Delta-8 assay measures marker Rho."},
        {"id": "x3", "document": "3", "text": "Marker Rho is produced by gland Uma."},
        {
            "id": "x4",
            "document": "4",
            "text": "Project Zenith does not use the Delta-8 assay. "
            "Project Zenith uses the Theta-3 assay.",
        },
        {"id": "x5", "document": "5", "text": "The Theta-3 assay measures marker Tau."},
    ]


def test_source_name_graph_does_not_link_passages_only_because_they_share_a_verb():
    graph = api().name_graph(corpus())
    assert "x2" in graph["x1"]
    assert graph["x1"]["x2"] > graph["x1"]["x4"]
    assert "x3" in graph["x2"]


def test_walk_exposes_two_missing_hops_instead_of_reinforcing_all_seed_hits():
    graph = api().name_graph(corpus())
    path = api().walk(graph, "x1", ["x1", "x4", "x5"], steps=2)
    assert path == ["x1", "x2", "x3"]
    assert len(api().walk(graph, "x1", [], steps=1)) == 2
    with pytest.raises(ValueError):
        api().walk(graph, "x1", [], steps=3)


def test_isolated_seed_falls_back_to_baseline_without_inventing_graph_edges():
    graph = {"a": {}, "b": {}, "c": {}}
    assert api().walk(graph, "a", ["a", "b", "c"], steps=2) == ["a", "b", "c"]
