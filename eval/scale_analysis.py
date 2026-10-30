"""Complete denominators, paired uncertainty and cost tables for scale retrieval."""

import argparse
import csv
import io
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from eval.public_qa_data import MODELS
from eval.scale_retrieval import evaluation_id
from eval.scale_single import ROOT
from eval.source_role_run import read


def rate(numerator, denominator):
    return numerator / denominator if denominator else None


def summary_flags(row):
    result = row.get("result") or {}
    blind, semantic = result.get("blind") or {}, result.get("semantic") or {}
    correct = semantic.get("semantic") == "correct"
    supported = correct and blind.get("reader_support") == "supported"
    return correct, supported, bool(blind), bool(semantic)


def summarize(rows):
    answerable = [r for r in rows if r["answerable"]]
    values = {"questions": len(rows), "answerable_questions": len(answerable)}
    for name, subset in (("all", rows), ("answerable", answerable)):
        correct = sum(summary_flags(r)[0] for r in subset)
        supported = sum(summary_flags(r)[1] for r in subset)
        unresolved = sum(not summary_flags(r)[3] for r in subset)
        blind_unresolved = sum(not summary_flags(r)[2] for r in subset)
        supported_unresolved = sum(not all(summary_flags(r)[2:]) for r in subset)
        values.update(
            {
                name + "_semantic_correct": correct,
                name + "_semantic_rate": rate(correct, len(subset)),
                name + "_supported_correct": supported,
                name + "_supported_rate": rate(supported, len(subset)),
                name + "_semantic_unresolved": unresolved,
                name + "_semantic_valid_rate": rate(correct, len(subset) - unresolved),
                name + "_semantic_upper_bound": rate(correct + unresolved, len(subset)),
                name + "_blind_unresolved": blind_unresolved,
                name + "_supported_unresolved": supported_unresolved,
                name + "_supported_upper_bound": rate(
                    supported + supported_unresolved, len(subset)
                ),
            }
        )
    values["semantic_valid"] = sum(summary_flags(r)[3] for r in rows)
    values["blind_valid"] = sum(summary_flags(r)[2] for r in rows)
    values["reader_protocol_valid"] = sum(
        (r.get("result") or {}).get("reader", {}).get("protocol_valid", False) for r in rows
    )
    for field, target, label in (
        ("sufficiency", "sufficient", "sufficient"),
        ("sufficiency", "partial", "partial_evidence"),
        ("relevance", "useful", "useful"),
        ("citation_support", "supported", "citation_supported"),
        ("reader_support", "unsupported", "reader_unsupported"),
    ):
        count = sum(((r.get("result") or {}).get("blind") or {}).get(field) == target for r in rows)
        values[label + "_count"] = count
        values[label + "_rate"] = rate(count, len(rows))
        answerable_count = sum(
            ((r.get("result") or {}).get("blind") or {}).get(field) == target for r in answerable
        )
        values["answerable_" + label + "_count"] = answerable_count
        values["answerable_" + label + "_rate"] = rate(answerable_count, len(answerable))
    values["partial_answer_count"] = sum(
        ((r.get("result") or {}).get("semantic") or {}).get("semantic") == "partially_correct"
        for r in answerable
    )
    values["graded_semantic_score"] = rate(
        values["answerable_semantic_correct"] + 0.5 * values["partial_answer_count"],
        len(answerable),
    )
    answered = [
        r for r in rows if (r.get("result") or {}).get("reader", {}).get("abstain") is False
    ]
    values["answered_count"] = len(answered)
    values["citation_supported_answered_count"] = sum(
        ((r.get("result") or {}).get("blind") or {}).get("citation_support") == "supported"
        for r in answered
    )
    values["citation_supported_among_answered"] = rate(
        values["citation_supported_answered_count"], values["answered_count"]
    )
    noanswer = [r for r in rows if not r["answerable"]]
    values["original_noanswer_questions"] = len(noanswer)
    for prefix, subset in (("all", rows), ("original_noanswer", noanswer)):
        labels = Counter(
            ((r.get("result") or {}).get("semantic") or {}).get("semantic", "unresolved")
            for r in subset
        )
        values[prefix + "_semantic_labels"] = dict(labels)
        successes = labels["correct"] + labels["valid_abstention"]
        values[prefix + "_semantic_success_count"] = successes
        values[prefix + "_semantic_success_rate"] = rate(successes, len(subset))
    values["original_noanswer_supported_correct"] = sum(summary_flags(r)[1] for r in noanswer)
    coverage = [
        r["diagnostics"]["reference_coverage"]["complete"]
        for r in rows
        if r["diagnostics"]["reference_coverage"]["complete"] is not None
    ]
    values["reference_complete_count"] = sum(coverage)
    values["reference_complete_rate"] = rate(sum(coverage), len(coverage))
    for field in ("source_count", "content_chars", "retrieval_seconds"):
        numbers = [r["diagnostics"][field] for r in rows]
        values[field + "_mean"] = float(np.mean(numbers)) if numbers else None
        values[field + "_p95"] = float(np.percentile(numbers, 95)) if numbers else None
    values["errors"] = dict(
        Counter(
            k + ":" + v for r in rows for k, v in (r.get("result") or {}).get("errors", {}).items()
        )
    )
    values["reference_status"] = dict(
        Counter(
            ((r.get("result") or {}).get("semantic") or {}).get("reference_status", "unresolved")
            for r in rows
        )
    )
    for field in ("path_status", "question_status"):
        values[field] = dict(
            Counter(
                ((r.get("result") or {}).get("blind") or {}).get(field, "unresolved") for r in rows
            )
        )
    return values


def paired(base, graph, *, metric="supported"):
    if metric not in {"supported", "sufficient", "useful"}:
        raise ValueError("unknown paired metric")

    def success(row):
        if metric == "supported":
            return summary_flags(row)[1]
        blind = (row.get("result") or {}).get("blind") or {}
        return blind.get("sufficiency" if metric == "sufficient" else "relevance") == metric

    baseline = {r["id"]: r for r in base if r["answerable"]}
    differences, gains, losses, excluded = [], 0, 0, 0
    for row in graph:
        if not row["answerable"] or row["id"] not in baseline:
            continue
        other = baseline[row["id"]]
        a, b = summary_flags(other), summary_flags(row)
        if not (a[2] and b[2] and (metric != "supported" or a[3] and b[3])):
            excluded += 1
            continue
        difference = int(success(row)) - int(success(other))
        differences.append(difference)
        gains += difference > 0
        losses += difference < 0
    result = {
        "paired_questions": len(differences),
        "excluded_unresolved": excluded,
        "gain": gains,
        "loss": losses,
        "delta": None,
        "ci95": None,
    }
    if differences:
        rng = np.random.default_rng(20261021)
        values = np.asarray(differences, dtype=float)
        draws = np.mean(values[rng.integers(0, len(values), size=(5000, len(values)))], axis=1)
        result.update(delta=float(values.mean()), ci95=np.percentile(draws, [2.5, 97.5]).tolist())
    return result


def load_rows(root, arm, directory="evaluations"):
    packets = root / "packets" / (arm + ".json")
    if not packets.exists():
        return []
    result = []
    for packet in read(packets):
        path = root / directory / (evaluation_id(packet) + ".json")
        result.append({**packet, "result": read(path) if path.exists() else None})
    return result


def accounting(directories):
    from eval.source_role_eval import digest

    seen = {}
    groups = defaultdict(
        lambda: {
            "attempts": 0,
            "known_rmb": 0.0,
            "unknown_cost_attempts": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "errors": Counter(),
            "seconds": [],
        }
    )
    for directory in directories:
        if not directory.exists():
            continue
        for launch in directory.glob("*.launch.json"):
            original = read(launch)
            result_file = launch.with_name(launch.name.replace(".launch.json", ".result.json"))
            result = read(result_file) if result_file.exists() else {}
            identity = digest(original)
            if identity in seen:
                if seen[identity] != digest(result):
                    raise ValueError("reused launch has inconsistent response")
                continue
            seen[identity] = digest(result)
            model = original["payload"]["model"]
            stage = original["label"].split("/")[0]
            row = groups[(directory.parent.name + "/" + directory.name, stage, model)]
            row["attempts"] += 1
            if result.get("actual_rmb") is None:
                row["unknown_cost_attempts"] += 1
            else:
                row["known_rmb"] += result["actual_rmb"]
            if result.get("seconds") is not None:
                row["seconds"].append(result["seconds"])
            usage = result.get("usage") or {}
            for k in ("prompt_tokens", "completion_tokens"):
                if type(usage.get(k)) is int:
                    row[k] += usage[k]
            error = result.get("error") if result_file.exists() else "ambiguous_launch"
            if error:
                row["errors"][error] += 1
    output = []
    for (phase, stage, model), row in sorted(groups.items()):
        times = row.pop("seconds")
        output.append(
            {
                "phase": phase,
                "stage": stage,
                "model": model,
                **row,
                "latency_p50_seconds": float(np.median(times)) if times else None,
                "latency_p95_seconds": float(np.percentile(times, 95)) if times else None,
            }
        )
    return output


def analyze(root, directory="evaluations"):
    qualification = read(root / "qualification.json")
    initial = read(root / "initial.json")
    arms = ["RAG", *(m for m in MODELS if qualification[m]["eligible"])]
    rows = {arm: load_rows(root, arm, directory) for arm in arms}
    output = {
        "qualification": qualification,
        "judgment_directory": directory,
        "arms": {},
        "paired_vs_RAG": {},
        "paired_evidence_vs_RAG": {},
        "construction": {},
        "audit": {},
        "baseline_question_clarity": {},
        "matrix": [],
    }
    clarity_by_id = {
        row["id"]: ((row.get("result") or {}).get("blind") or {}).get(
            "question_status", "unresolved"
        )
        for row in rows.get("RAG", [])
    }
    for arm, records in rows.items():
        if not records:
            continue
        output["arms"][arm] = {
            "all": summarize(records),
            "by_dataset": {
                d: summarize([r for r in records if r["dataset"] == d])
                for d in sorted({r["dataset"] for r in records})
            },
            "by_difficulty": {
                d: summarize([r for r in records if r["difficulty"] == d])
                for d in sorted({r["difficulty"] for r in records})
            },
        }
        if arm != "RAG":
            output["paired_vs_RAG"][arm] = paired(rows["RAG"], records)
            output["paired_evidence_vs_RAG"][arm] = {
                metric: paired(rows["RAG"], records, metric=metric)
                for metric in ("sufficient", "useful")
            }
        # Post-hoc diagnostic: every arm uses the same baseline-defined question subset.
        output["baseline_question_clarity"][arm] = {
            status: summarize([r for r in records if clarity_by_id.get(r["id"]) == status])
            for status in sorted(set(clarity_by_id.values()))
        }
        audit_records = {r["id"]: r for r in load_rows(root, arm, "audit") if r["result"]}
        agreement = Counter()
        disagreements = []
        for row in records:
            other = audit_records.get(row["id"])
            if not other or not row["result"]:
                continue
            for group, field in (
                ("blind", "sufficiency"),
                ("blind", "reader_support"),
                ("semantic", "semantic"),
            ):
                one, two = row["result"].get(group), other["result"].get(group)
                if one and two:
                    agreement[group + "/" + field + "/n"] += 1
                    agreement[group + "/" + field + "/agree"] += one[field] == two[field]
                    if one[field] != two[field]:
                        disagreements.append(
                            {
                                "id": row["id"],
                                "field": group + "/" + field,
                                "primary": one[field],
                                "secondary": two[field],
                            }
                        )
        output["audit"][arm] = {"counts": dict(agreement), "disagreements": disagreements}
    for model in MODELS:
        docs = [read(p) for p in (root / "construction" / model).glob("*.json")]
        output["construction"][model] = {
            "initial_facts": sum(len(items) for items in initial[model].values()),
            "initial_sources_with_facts": sum(bool(items) for items in initial[model].values()),
            "attempted_sources": len(docs),
            "valid_source_outputs": sum(not r["error"] for r in docs),
            "sources_with_facts": sum(bool(r["facts"]) for r in docs),
            "facts": sum(len(r["facts"]) for r in docs),
            "rejected_facts": sum(len(r["rejections"]) for r in docs),
            "errors": dict(Counter(r["error"] for r in docs if r["error"])),
        }
        for method, arm in (("RAG", "RAG"), ("Graph+RAG+source correction", model)):
            stats = output["arms"].get(arm, {}).get("all", {})
            output["matrix"].append(
                {
                    "model": model,
                    "method": method,
                    "eligible": method == "RAG" or qualification[model]["eligible"],
                    **{k: v for k, v in stats.items() if not isinstance(v, dict)},
                }
            )
    reused = []
    if (root / "reuse-manifest.json").exists():
        previous = Path(read(root / "reuse-manifest.json")["previous_root"])
        reused = [previous / folder for folder in ("calls", "evaluation-calls", "reasoned-calls")]
    output["costs"] = accounting(
        [
            Path(".cache/full-scale-protocol-2026-10-21-v1/calls"),
            Path(".cache/full-scale-retrieval-2026-10-21-v1/calls"),
            *reused,
            root / "calls",
            root / "evaluation-calls",
            root / "reasoned-calls",
        ]
    )
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--judge", choices=("original", "reasoned"), default="original")
    args = parser.parse_args()
    value = analyze(args.root, "reasoned" if args.judge == "reasoned" else "evaluations")
    suffix = "-reasoned" if args.judge == "reasoned" else ""
    target = args.root / ("analysis" + suffix + ".json")
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    keys = list(dict.fromkeys(k for row in value["matrix"] for k in row))
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=keys)
    writer.writeheader()
    writer.writerows(value["matrix"])
    (args.root / ("retrieve-quality" + suffix + ".csv")).write_text(stream.getvalue())
    directory = "reasoned" if args.judge == "reasoned" else "evaluations"
    case_rows = []
    for arm in value["arms"]:
        for row in load_rows(args.root, arm, directory):
            result = row.get("result") or {}
            reader = result.get("reader") or {}
            blind = result.get("blind") or {}
            semantic = result.get("semantic") or {}
            case_rows.append(
                {
                    "arm": arm,
                    "id": row["id"],
                    "dataset": row["dataset"],
                    "answerable": row["answerable"],
                    "question": row["question"],
                    "reference_answers": row["answers"],
                    "answer": reader.get("answer"),
                    "abstain": reader.get("abstain"),
                    "protocol_valid": reader.get("protocol_valid"),
                    "semantic": semantic.get("semantic"),
                    "reference_status": semantic.get("reference_status"),
                    "sufficiency": blind.get("sufficiency"),
                    "relevance": blind.get("relevance"),
                    "reader_support": blind.get("reader_support"),
                    "citation_support": blind.get("citation_support"),
                    "source_count": len(row["evidence"]["sources"]),
                    "path_count": len(row["evidence"]["paths"]),
                    "citations": reader.get("citations"),
                    "retrieved_source_ids": [s["id"] for s in row["evidence"]["sources"]],
                    "relation_paths": row["evidence"]["paths"],
                    "path_status": blind.get("path_status"),
                    "question_status": blind.get("question_status"),
                    "errors": result.get("errors"),
                    "semantic_reason": semantic.get("reason"),
                    "evidence_reason": blind.get("reason"),
                }
            )
    (args.root / ("case-results" + suffix + ".json")).write_text(
        json.dumps(case_rows, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "arms": {k: v["all"] for k, v in value["arms"].items()},
                "construction": value["construction"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
