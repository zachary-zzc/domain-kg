"""Reproducible source-cluster uncertainty and complete retrieval denominators."""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from eval.kg_cloudflare import accounting as cloudflare_accounting
from eval.kg_comparison import ARMS, CLOUD, PREVIOUS, ROOT
from eval.kg_inventory_recovery import inventory_check
from eval.kg_retrieval_extension import ARMS as RETRIEVAL_ARMS
from eval.kg_supplemental import models
from eval.scale_analysis import accounting, load_rows, paired, rate, summarize
from eval.source_role_run import read


def summarize_quality(rows):
    total = sum(r["facts"] for r in rows)
    judged = sum(r["judged"] for r in rows)
    supported = sum(r["supported"] for r in rows)
    eligible = [r for r in rows if r["reference_count"] is not None]
    refs = sum(r["reference_count"] for r in eligible)
    covered = sum(r["covered"] for r in eligible)
    possible_coverage = sum(
        r["reference_count"] if r["facts"] > r["judged"] else r["covered"] for r in eligible
    )
    metadata = all(r.get("construction_metadata_available", True) for r in rows)
    return {
        "sources": len(rows),
        "nonempty_sources": sum(r["nonempty"] for r in rows),
        "construction_errors": sum(bool(r["error"]) for r in rows) if metadata else None,
        "accepted_facts": total,
        "judged_facts": judged,
        "supported_facts": supported,
        "unresolved_facts": total - judged,
        "support_rate": rate(supported, judged),
        "support_lower": rate(supported, total),
        "support_upper": rate(supported + total - judged, total),
        "coverage_sources": len(eligible),
        "references": refs,
        "covered_references": covered,
        "coverage_rate": rate(covered, refs),
        "coverage_upper": rate(possible_coverage, refs),
        "rejected_proposals": sum(r["rejected"] for r in rows) if metadata else None,
        "raw_proposals_valid_envelopes": (
            sum(r["raw_count"] or 0 for r in rows) if metadata else None
        ),
    }


def intervals(rows, other=None):
    rng = np.random.default_rng(20261021)
    draws = {"support_rate": [], "coverage_rate": []}
    for ids in rng.integers(0, len(rows), size=(5000, len(rows))):
        a = summarize_quality([rows[i] for i in ids])
        b = summarize_quality([other[i] for i in ids]) if other is not None else None
        for metric in draws:
            value = a[metric]
            if value is not None and (b is None or b[metric] is not None):
                draws[metric].append(value - (b[metric] if b is not None else 0))
    return {m: np.percentile(v, [2.5, 97.5]).tolist() if v else None for m, v in draws.items()}


def source_rows(root, model, strict=True):
    grouped = defaultdict(list)
    fact_details = []
    agreement = Counter()
    initial = read(PREVIOUS / "initial.json")
    for doc in read(root / "sources.json"):
        stages = list(ARMS) + (["full_initial", "full_corrected"] if model in CLOUD else [])
        idx = root / "fact-audit" / model / (doc["id"] + ".index.json")
        if not idx.exists():
            if strict:
                raise ValueError("missing audit " + str(idx))
            continue
        index = read(idx)
        facts = {f["id"]: f for f in index["facts"]}
        judgments = {}
        secondary = {}
        for directory, target in [("fact-audit", judgments), ("fact-audit-secondary", secondary)]:
            for p in (root / directory / model).glob(doc["id"] + ".[0-9]*.json"):
                record = read(p)
                if not record["error"]:
                    target.update({f["id"]: f for f in record["facts"]})
        if strict:
            for i in range(len(index["batches"])):
                if not (
                    root / "fact-audit" / model / (doc["id"] + "." + str(i) + ".json")
                ).exists():
                    raise ValueError("unfinished audit batch")
        check = inventory_check(root, doc)
        refs = (
            {f["id"] for f in check["facts"] if f["verdict"] == "supported"}
            if not check["error"]
            else None
        )
        for arm in stages:
            if arm == "full_initial":
                original = initial[model].get(doc["id"], [])
                record = {
                    "facts": original,
                    "error": None,
                    "rejections": [],
                    "raw_count": len(original),
                }
            elif arm == "full_corrected":
                record = read(PREVIOUS / "construction" / model / (doc["id"] + ".json"))
            else:
                record = read(root / "construction" / model / arm / (doc["id"] + ".json"))
            ids = index["mapping"][arm]
            selected = [judgments[i] for i in ids if i in judgments]
            supported = [f for f in selected if f["verdict"] == "supported"]
            covered = {r for f in supported for r in f["matches"]}
            row = {
                "model": model,
                "arm": arm,
                "source": doc["id"],
                "dataset": doc["dataset"],
                "facts": len(ids),
                "judged": len(selected),
                "supported": len(supported),
                "covered": len(covered & refs) if refs is not None else None,
                "reference_count": len(refs) if refs is not None else None,
                "nonempty": bool(ids),
                "error": record["error"],
                "rejected": len(record["rejections"]),
                "raw_count": record["raw_count"],
                "construction_metadata_available": arm != "full_initial",
            }
            grouped[arm].append(row)
            for ident in ids:
                f = facts[ident]
                j = judgments.get(ident)
                j2 = secondary.get(ident)
                fact_details.append(
                    {
                        "model": model,
                        "arm": arm,
                        "source": doc["id"],
                        "dataset": doc["dataset"],
                        "title": doc["title"],
                        "fact_id": ident,
                        **f,
                        "judgment": j,
                        "secondary": j2,
                    }
                )
        for ident, j in judgments.items():
            if ident in secondary:
                agreement["pairs"] += 1
                agreement["same_verdict"] += j["verdict"] == secondary[ident]["verdict"]
                agreement["same_supported"] += (j["verdict"] == "supported") == (
                    secondary[ident]["verdict"] == "supported"
                )
    return dict(grouped), fact_details, dict(agreement)


def analyze(root, partial=False):
    quality = []
    deltas = []
    sources = []
    facts = []
    agreements = []
    for model in models(root):
        groups, detail, agreement = source_rows(root, model, strict=not partial)
        facts.extend(detail)
        agreements.append({"model": model, **agreement})
        for arm, rows in groups.items():
            quality.append(
                {"model": model, "arm": arm, **summarize_quality(rows), "ci95": intervals(rows)}
            )
            sources.extend(rows)
        for comparison, baseline in [
            ("source_repair", "direct"),
            ("context_repair", "source_repair"),
            ("context_repair", "direct"),
            ("full_corrected", "full_initial"),
        ]:
            if comparison in groups and baseline in groups:
                a, b = map(summarize_quality, (groups[comparison], groups[baseline]))
                deltas.append(
                    {
                        "model": model,
                        "arm": comparison,
                        "baseline": baseline,
                        "delta": {
                            m: a[m] - b[m] if a[m] is not None and b[m] is not None else None
                            for m in ("support_rate", "coverage_rate")
                        },
                        "ci95": intervals(groups[comparison], groups[baseline]),
                        "sources": len(groups[comparison]),
                    }
                )
    retrieval = {}
    for arm in ("RAG", *CLOUD):
        retrieval[arm] = load_rows(PREVIOUS, arm, directory="reasoned")
    for arm in RETRIEVAL_ARMS:
        retrieval[arm] = load_rows(root, arm, directory="retrieval-results")
        if not partial and (
            len(retrieval[arm]) != 1000 or any(r["result"] is None for r in retrieval[arm])
        ):
            raise ValueError("incomplete retrieval " + arm)
    comparisons = []
    for model in CLOUD:
        arm = "KG3+RAG--" + model
        for a, b in [(model, arm), (arm, "RAG")]:
            for metric in ("supported", "sufficient", "useful"):
                comparisons.append(
                    {
                        "arm": a,
                        "baseline": b,
                        "metric": metric,
                        **paired(retrieval[b], retrieval[a], metric=metric),
                    }
                )
    for a, b in [
        ("KG3+RAG--qwen3.6-35b-a3b", "KG3-only--qwen3.6-35b-a3b"),
        ("qwen3.6-35b-a3b", "KG3-only--qwen3.6-35b-a3b"),
    ]:
        for metric in ("supported", "sufficient", "useful"):
            comparisons.append(
                {
                    "arm": a,
                    "baseline": b,
                    "metric": metric,
                    **paired(retrieval[b], retrieval[a], metric=metric),
                }
            )
    dirs = [p for p in root.iterdir() if p.is_dir() and p.name.endswith("-calls")]
    costs = accounting([p for p in dirs if not p.name.startswith("cloudflare-")])
    result = {
        "complete": not partial,
        "quality": quality,
        "construction_deltas": deltas,
        "source_rows": sources,
        "fact_rows": facts,
        "judge_agreement": agreements,
        "retrieval": {a: summarize(v) for a, v in retrieval.items()},
        "retrieval_datasets": {
            a: {
                d: summarize([r for r in v if r["dataset"] == d])
                for d in ("hotpot", "musique", "drop", "squad")
            }
            for a, v in retrieval.items()
        },
        "retrieval_paired": comparisons,
        "incremental_costs": costs,
        "cloudflare_costs": cloudflare_accounting(root),
        # Full originals remain in corpus.json and immutable packets. Avoid copying
        # the same paragraphs thousands of times into the analysis/workbook input.
        "retrieval_case_rows": [
            {
                **{
                    k: value for k, value in r.items() if k not in {"evidence", "reference_sources"}
                },
                "arm": a,
                "source_ids": [s["id"] for s in r["evidence"]["sources"]],
                "paths": r["evidence"]["paths"],
                "reference_source_ids": [s["id"] for s in r["reference_sources"]],
            }
            for a, v in retrieval.items()
            for r in v
        ],
    }
    (root / ("analysis-progress.json" if partial else "analysis.json")).write_text(
        json.dumps(result, ensure_ascii=False, indent=2)
    )
    print(
        json.dumps(
            {
                "quality_rows": len(quality),
                "fact_rows": len(facts),
                "retrieval_arms": len(retrieval),
                "complete": not partial,
            }
        )
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, default=ROOT)
    p.add_argument("--partial", action="store_true")
    a = p.parse_args()
    analyze(a.root, a.partial)


if __name__ == "__main__":
    main()
