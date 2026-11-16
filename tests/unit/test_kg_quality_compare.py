import json

import pytest
from test_kg_quality_run import cases, docs, graph

from eval import kg_quality as q
from eval import kg_quality_run as run
from eval.kg_quality_compare import compare, render


async def fixture_runs(tmp_path):
    model = q.WORK_MODELS[-1]
    g = {**graph(), "model": model, "construction": {"windows": 2, "failed_windows": 0}}
    ranks = q.rank_bundle(cases(), docs(), [["a", "b"]])
    packets = await run.retrieve_packets(cases(), docs(), g, ranks, model=model)
    roots = (tmp_path / "before", tmp_path / "after")
    for root in roots:
        for name, value in {
            "cases.json": cases(),
            "corpus.json": docs(),
            "windows.json": [],
            "protocol.json": {**q.PROTOCOL, "working_models": [model]},
            "ranks.json": ranks,
            "packets.json": packets,
            "provenance.json": {"scope": "fixture"},
            "frozen.json": {"code": run.code_hashes()},
            f"construction/{model}/graph-statistics.json": run.graph_statistics(docs(), g),
        }.items():
            run.save_once(root / name, value)
        run.report(root)
    return roots


async def test_algorithm_comparison_preserves_unknown_pairs(tmp_path):
    roots = await fixture_runs(tmp_path)
    result = compare(*roots)
    model = q.WORK_MODELS[-1]
    metric = result["models"][model]["algorithm_change"]["grounded_correct"]
    assert metric["known_pairs"] == 0
    assert metric["unknown_pairs"] == 1
    assert metric["paired_delta"] is None
    assert metric["delta_bounds_all_planned"] == [-1, 1]
    assert "unknown" in render(result)


@pytest.mark.parametrize("change", ["vector", "contract", "missing_cell"])
async def test_algorithm_comparison_rejects_uncontrolled_changes(tmp_path, change):
    before, after = await fixture_runs(tmp_path)
    if change == "contract":
        name = "frozen.json"
        value = run.read(after / name)
        value["code"]["eval/kg_quality.py"] = "changed"
    else:
        name = "scored-rows.json"
        value = run.read(after / name)
        if change == "vector":
            next(r for r in value if r["arm"] == "vector_rag")["scores"]["grounded_correct"] = 1
        else:
            value.pop()
    (after / name).write_text(json.dumps(value))
    with pytest.raises(ValueError, match="changed|cells"):
        compare(before, after)


async def test_prompt_comparison_allows_only_declared_construction_policy_change(tmp_path):
    before, after = await fixture_runs(tmp_path)
    protocol = run.read(after / "protocol.json")
    protocol["construction_semantics"] = "source_facets_v1"
    (after / "protocol.json").write_text(json.dumps(protocol))
    result = compare(before, after)
    assert result["construction_semantics"] == {
        "before": "source_roles_v1", "after": "source_facets_v1"
    }
    protocol["max_content_chars"] = 20000
    (after / "protocol.json").write_text(json.dumps(protocol))
    with pytest.raises(ValueError, match="protocol changed"):
        compare(before, after)
