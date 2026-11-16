"""Compare two frozen algorithm runs without changing semantic scoring or unknowns."""

import argparse
import json
from pathlib import Path

from eval import kg_quality as q
from eval.kg_quality_build import CONSTRUCTION_SEMANTICS
from eval.kg_quality_run import file_hash, selected_models
from eval.source_role_run import read


def compare(before, after):
    for name in ("cases.json", "corpus.json", "windows.json", "ranks.json"):
        if read(before / name) != read(after / name):
            raise ValueError(f"comparison inputs differ: {name}")
    protocols = [read(p / "protocol.json") for p in (before, after)]
    policies = [p.pop("construction_semantics", "source_roles_v1") for p in protocols]
    if protocols[0] != protocols[1] or any(p not in CONSTRUCTION_SEMANTICS for p in policies):
        raise ValueError("comparison evaluation protocol changed")
    old_code, new_code = (read(p / "frozen.json")["code"] for p in (before, after))
    for name in ("eval/kg_quality.py", "eval/kg_quality_api.py", "eval/scale_retrieval.py"):
        if old_code[name] != new_code[name] or file_hash(Path(name)) != old_code[name]:
            raise ValueError(f"evaluation contract changed: {name}")
    cases = read(before / "cases.json")
    rows = [read(p / "scored-rows.json") for p in (before, after)]
    packets = [read(p / "packets.json") for p in (before, after)]
    summaries = [read(p / "summary.json") for p in (before, after)]
    models = selected_models(before)
    expected = {(m, c["id"], a) for m in models for c in cases for a in q.ARMS}
    for values in (*rows, *packets):
        keys = {(r["model"], r["id"], r["arm"]) for r in values}
        if len(values) != len(expected) or keys != expected:
            raise ValueError("incomplete or duplicate planned comparison cells")
    for values in (rows, packets):
        dense = [
            {(r["model"], r["id"]): r for r in group if r["arm"] == "vector_rag"}
            for group in values
        ]
        for key in dense[0]:
            field = "evidence" if "evidence" in dense[0][key] else "scores"
            if dense[0][key][field] != dense[1][key][field]:
                raise ValueError("vector control changed between algorithm runs")
    result = {
        "scope": "paired development comparison; no held-out or significance claim",
        "inputs_identical": True,
        "vector_control_identical": True,
        "construction_semantics": dict(zip(("before", "after"), policies, strict=True)),
        "models": {},
        "artifacts": {
            str(p / name): file_hash(p / name)
            for p in (before, after)
            for name in ("summary.json", "scored-rows.json", "frozen.json", "packets.json")
        },
    }
    for model in models:
        paired_rows = [
            {**r, "arm": label}
            for label, group in zip(("before", "after"), rows, strict=True)
            for r in group
            if r["model"] == model and r["arm"] == "domain_kg"
        ]
        ids = {c["id"] for c in cases}
        result["models"][model] = {
            "algorithm_change": {
                m: q.paired(paired_rows, ids, "before", "after", m)
                for m in q.score(None, None, None)
            },
            "methods": {
                label: summary["models"][model]["groups"]["all"]
                for label, summary in zip(("before", "after"), summaries, strict=True)
            },
            "graph": {
                label: summary["graph_statistics"][model]
                for label, summary in zip(("before", "after"), summaries, strict=True)
            },
        }
    return result


def render(result):
    def percent(value):
        return "unknown" if value is None else f"{100 * value:.1f}%"

    lines = [
        "# 算法配对实验",
        "",
        "固定开发集与语义裁判；未知值保留。两次运行的 vector RAG 输入和分数一致。",
        "",
        "| 模型 | 算法 | 方法 | 证据充分@12 | 语义覆盖@12 | 来源精度@12 | "
        "语义答对 | 有依据答对 | 完整度 | 引用支持 | 无依据断言率 | 已知回答/计划 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    metrics = [
        *q.RETRIEVAL_METRICS,
        "semantic_correct",
        "grounded_correct",
        "answer_completeness",
        "citation_supported",
        "unsupported_claim_rate",
    ]
    for model, value in result["models"].items():
        for label, group in value["methods"].items():
            for arm, row in group["arms"].items():
                scores = " | ".join(percent(row[m]["mean_known"]) for m in metrics)
                known = row["grounded_correct"]
                lines.append(
                    f"| {model} | {label} | {arm} | {scores} | "
                    f"{known['known']}/{known['planned']} |"
                )
    lines += [
        "",
        "| 模型 | 算法 | 节点观察 | 关系观察 | 有关系文档 | 孤立节点 | 谓词种类 | 失败窗口 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for model, value in result["models"].items():
        for label, g in value["graph"].items():
            lines.append(
                f"| {model} | {label} | {g['node_observations']} | {g['relation_observations']} | "
                f"{g['documents_with_relations']}/{g['documents']} | "
                f"{g['observation_topology']['isolates']} | {g['predicate_type_count']} | "
                f"{g['construction']['failed_windows']} |"
            )
    lines += [
        "",
        "图结构不等于关系语义准确率。domain-kg 包含 BM25，不能把与纯向量的全部差异归因于 KG。",
        "逐题配对增减、未知值的全计划上下界及结构统计见 comparison.json；"
        "各个检索 k、数据集分层、调用记录与耗时见原始运行 summary.json。",
    ]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = compare(args.before, args.after)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "comparison.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    (args.output / "comparison.md").write_text(render(result))


if __name__ == "__main__":
    main()
