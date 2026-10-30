"""Reproducible summaries of the complete, offline native semantic review."""

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path

from eval.public_qa_prepare import file_hash, save
from eval.semantic_review import ROOT

MODELS = (
    "qwen3.5-397b-a17b",
    "qwen3.5-122b-a10b",
    "qwen3.6-35b-a3b",
    "qwen3.8-27b",
    "deepseek-r1-distill-qwen-1.5b",
)
ARMS = ("V6", "V12", "G6", "U12")


def common_questions(rows, models, arms):
    seen, cohorts = set(), {(m, a): set() for m in models for a in arms}
    for row in rows:
        key = row["model"], row["arm"], row["case_id"]
        if key in seen:
            raise ValueError("duplicate logical model/arm/question response")
        seen.add(key)
        cohorts[key[:2]].add(key[2])
    return set.intersection(*cohorts.values())


def validate_review(packets, pairs, labels, quality):
    groups = {g["key"]: g for c in packets for g in c["groups"]}
    owners = {g["key"]: c["id"] for c in packets for g in c["groups"]}
    if set(groups) != set(labels) or any(v["judgment"] not in "cpwau" for v in labels.values()):
        raise ValueError("missing, extra or invalid native judgment")
    if set(quality) != {c["id"] for c in packets} or not all(
        q["reviewed"] for q in quality.values()
    ):
        raise ValueError("missing question quality review")
    members = {i: g["key"] for g in groups.values() for i in g["members"]}
    ids = [p["identity"] for p in pairs]
    if len(ids) != len(set(ids)) or len(ids) != sum(len(g["members"]) for g in groups.values()):
        raise ValueError("duplicate response identity/member")
    if members != {p["identity"]: p["group"] for p in pairs}:
        raise ValueError("response/group membership mismatch")
    if any(owners[p["group"]] != p["case_id"] for p in pairs):
        raise ValueError("response assigned to a different question")


def wilson(k, n):
    if not n:
        return None
    z, p = 1.959963984540054, k / n
    d = 1 + z * z / n
    center = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [max(0, center - half), min(1, center + half)]


def statistics(rows, labels):
    n = len(rows)
    counts = Counter(labels[r["group"]]["judgment"] for r in rows)
    cites = [r["citation"] for r in rows]
    scored = [c for c in cites if c["complete"] is not None]
    complete = sum(c["complete"] for c in scored)
    uncertain = sum(c["status"] == "ambiguous" for c in scored)
    old = sum((r["old_scores"] or {}).get("em", 0) for r in rows)
    return {
        "n": n,
        "content_counts": {k: counts[k] for k in "cpwau"},
        "content_accuracy": counts["c"] / n if n else None,
        "content_wilson95": wilson(counts["c"], n),
        "old_em": old / n if n else None,
        "original_parser_or_call_errors": sum(bool(r["original_error"]) for r in rows),
        "citation_n": len(scored),
        "citation_complete_count": complete,
        "citation_complete_lower": complete / len(scored) if scored else None,
        "citation_complete_upper": (complete + uncertain) / len(scored) if scored else None,
        "citation_recall": sum(c["recall"] for c in scored) / len(scored) if scored else None,
        "citation_precision": sum(c["precision"] for c in scored) / len(scored) if scored else None,
        "citation_statuses": dict(Counter(c["status"] for c in cites)),
        "citation_recovered_arrays": sum(c["recovery"] == "explicit_array" for c in cites),
        "citation_with_invalid_ids": sum(bool(c["invalid_ids"]) for c in cites),
        "correct_and_complete": sum(
            labels[r["group"]]["judgment"] == "c" and r["citation"]["complete"] == 1 for r in rows
        ),
    }


def paired_changes(rows, labels, cohort, model, base, dual):
    matrix = {
        (r["case_id"], r["arm"]): labels[r["group"]]["judgment"] == "c"
        for r in rows
        if r["model"] == model and r["case_id"] in cohort
    }
    if any((q, arm) not in matrix for q in cohort for arm in (base, dual)):
        raise ValueError("missing paired response")
    wins = sum(matrix[q, dual] and not matrix[q, base] for q in cohort)
    losses = sum(matrix[q, base] and not matrix[q, dual] for q in cohort)
    n = len(cohort)
    # Descriptive normal interval for the mean of paired {-1,0,1} differences.
    delta = (wins - losses) / n if n else None
    se = math.sqrt(max(0, ((wins + losses) / n - delta * delta) / n)) if n else 0
    return {
        "n": n,
        "model": model,
        "base": base,
        "dual": dual,
        "wins": wins,
        "losses": losses,
        "ties": n - wins - losses,
        "delta": delta,
        "paired_descriptive95": [delta - 1.96 * se, delta + 1.96 * se] if n else None,
    }


def aggregate(root):
    out = root / "full-semantic-review"

    def read(name):
        return json.loads((out / name).read_text())

    packets, pairs, labels, quality = (
        read(n) for n in ("packets.json", "pairs.json", "labels.json", "question-quality.json")
    )
    validate_review(packets, pairs, labels, quality)
    common = common_questions(pairs, MODELS, ARMS)
    primary = {q for q in common if quality[q]["eligible_primary"]}
    if any(labels[r["group"]]["judgment"] == "u" for r in pairs if r["case_id"] in primary):
        raise ValueError("unresolved content in primary cohort")
    cases = {c["id"]: c for c in packets}
    result = {
        "responses": len(pairs),
        "questions": len(packets),
        "answer_groups": len(labels),
        "common_questions": len(common),
        "common_question_ids": sorted(common),
        "common_original_answerable": sum(cases[q]["answerable"] for q in common),
        "primary_questions": len(primary),
        "primary_question_ids": sorted(primary),
        "primary_by_dataset": dict(Counter(cases[q]["dataset"] for q in sorted(primary))),
        "flagged_questions": sum(bool(q["flags"]) for q in quality.values()),
        "flagged_original_answerable": sum(
            bool(quality[c["id"]]["flags"]) and c["answerable"] for c in packets
        ),
        "original_unanswerable_questions": sum(not c["answerable"] for c in packets),
        "models": {},
        "paired": [],
        "by_dataset": [],
        "by_difficulty": [],
        "coverage": [],
        "all_reviewed_dispositions": [],
        "new_cloud_inference_calls": 0,
        "limitations": [
            "One native assistant review; no independent expert/inter-rater reliability.",
            "Citation completeness covers original reference support; "
            "it does not measure claim-level entailment.",
            "Primary cohort excludes every flagged question for every model/arm.",
            "Non-primary labels may be conditional on the original intended interpretation.",
            "414 common questions are an unfinished subset of the planned 1000-question run.",
        ],
    }
    for model in MODELS:
        result["models"][model] = {}
        for arm in ARMS:
            rows = [r for r in pairs if r["model"] == model and r["arm"] == arm]
            subset = [r for r in rows if r["case_id"] in primary]
            result["models"][model][arm] = statistics(subset, labels)
            result["coverage"].append(
                {
                    "model": model,
                    "arm": arm,
                    "completed": len(rows),
                    "common": sum(r["case_id"] in common for r in rows),
                    "primary": len(subset),
                    "outside_common": sum(r["case_id"] not in common for r in rows),
                }
            )
            result["all_reviewed_dispositions"].append(
                {"model": model, "arm": arm, **statistics(rows, labels)}
            )
            for key, target in (("dataset", "by_dataset"), ("difficulty", "by_difficulty")):
                for name in sorted({cases[q][key] for q in primary}):
                    selected = [r for r in subset if cases[r["case_id"]][key] == name]
                    if selected:
                        result[target].append(
                            {"model": model, "arm": arm, key: name, **statistics(selected, labels)}
                        )
        for base in ("V6", "V12"):
            result["paired"].append(paired_changes(pairs, labels, primary, model, base, "U12"))
    result["review_files_sha256"] = {
        n: file_hash(out / n)
        for n in (
            "manifest.json",
            "packets.json",
            "pairs.json",
            "labels.json",
            "question-quality.json",
            "adjudication-v2.json",
        )
    }
    save(out / "summary.json", result)
    columns = (
        "identity",
        "case_id",
        "model",
        "arm",
        "group",
        "judgment",
        "eligible_primary",
        "quality_flags",
        "citation_complete",
        "citation_recall",
        "citation_precision",
        "citation_status",
        "original_parser_or_call_error",
    )
    with (out / "response-judgments.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for r in sorted(pairs, key=lambda p: (p["case_id"], p["model"], p["arm"])):
            writer.writerow(
                {
                    **{k: r[k] for k in columns[:5]},
                    "judgment": labels[r["group"]]["judgment"],
                    "eligible_primary": quality[r["case_id"]]["eligible_primary"],
                    "quality_flags": "|".join(quality[r["case_id"]]["flags"]),
                    **{
                        f"citation_{k}": r["citation"][k]
                        for k in ("complete", "recall", "precision", "status")
                    },
                    "original_parser_or_call_error": bool(r["original_error"]),
                }
            )
    issues = [
        {
            "index": c["index"],
            "id": c["id"],
            "dataset": c["dataset"],
            "question": c["question"],
            "original_answers": c["answers"],
            "original_answerable": c["answerable"],
            **quality[c["id"]],
            "source_evidence": c["contexts"],
            "original_support": c["support"],
        }
        for c in packets
        if quality[c["id"]]["flags"]
    ]
    save(out / "issues.json", issues)
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "responses",
                    "questions",
                    "answer_groups",
                    "common_questions",
                    "primary_questions",
                    "primary_by_dataset",
                    "flagged_questions",
                    "flagged_original_answerable",
                )
            },
            ensure_ascii=False,
        )
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    aggregate(parser.parse_args().root)
