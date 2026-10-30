"""Replay saved retrieval and diagnose construction/selection/reader bottlenecks."""

import argparse
import json
from collections import Counter
from pathlib import Path

from eval import dual_path_data as data
from eval import dual_path_retrieval as baseline
from eval.dual_path_run import graph_facts, load_rows, verify
from eval.dual_path_transport import logical_rows
from eval.public_qa_prepare import file_hash, save
from eval.retrieval_audit import (
    SourceBM25,
    fill_context,
    pool_context,
    supplied_units,
    tie_rank,
    trace_graph,
)
from eval.semantic_summary import ARMS, MODELS


def construction_audit(rows, batches, docs):
    facts = [f for r in rows for f in (r.get("graph") or {}).get("facts", [])]
    covered = Counter(f["d"] for f in facts)
    positions, rejects, sizes = {}, Counter(), Counter()
    for row in rows:
        batch = batches[int(row["label"])]
        for i, doc in enumerate(batch):
            counts = positions.setdefault(str(i), {"sources": 0, "covered": 0})
            counts["sources"] += 1
            counts["covered"] += doc["id"] in covered
        raw = (row.get("raw") or {}).get("choices", [{}])[0].get("message", {}).get("content")
        value = data.json_object(raw)
        if value is None:
            rejects["undecodable_outer_json_batches"] += 1
            continue
        if set(value) != {"facts"} or not isinstance(value["facts"], list):
            rejects["wrong_outer_shape_batches"] += 1
            continue
        raw_facts = value["facts"]
        sizes[str(len(raw_facts))] += 1
        if len(raw_facts) > len(batch) * 3:
            rejects["batch_exceeds_three_per_source_cap"] += 1
        for f in raw_facts:
            if not isinstance(f, dict) or set(f) != {"d", "s", "p", "o", "n", "q"}:
                rejects["record_wrong_shape"] += 1
                continue
            if not all(isinstance(v, str) for v in f.values()):
                rejects["record_wrong_type"] += 1
                continue
            source = next((d for i, d in enumerate(batch) if str(i) == f["d"]), None)
            if source is None:
                rejects["record_unknown_source_id"] += 1
                continue
            rejects["record_unknown_predicate"] += f["p"] not in data.PREDICATES
            rejects["record_invalid_polarity"] += f["n"] not in (
                "positive",
                "negative",
                "uncertain",
            )
            rejects["record_quote_length"] += not 0 < len(f["q"]) <= 400
            rejects["record_quote_not_literal"] += f["q"] not in source["text"]
            rejects["record_endpoint_not_literal"] += any(
                not 0 < len(f[k]) <= 160 or f[k] not in source["title"] + "\n" + source["text"]
                for k in ("s", "o")
            )
            rejects["record_same_endpoints"] += f["s"] == f["o"]
    return {
        "batches": len(rows),
        "facts": len(facts),
        "sources": len(docs),
        "sources_with_facts": len(covered),
        "source_coverage": len(covered) / len(docs),
        "facts_per_covered_source": len(facts) / max(1, len(covered)),
        "sources_at_three_fact_cap": sum(n == 3 for n in covered.values()),
        "usable_empty_batches": sum(
            r.get("graph") is not None and not r["graph"]["facts"] for r in rows
        ),
        "batch_positions": positions,
        "raw_fact_count_distribution": dict(sorted(sizes.items())),
        "rejection_flags_nonexclusive": dict(sorted(rejects.items())),
        "predicates": dict(sorted(Counter(f["p"] for f in facts).items())),
        "accepted_endpoints_not_both_in_quote": sum(
            f["s"] not in f["q"] or f["o"] not in f["q"] for f in facts
        ),
        "note": (
            "Literal grounding is not a semantic-precision measurement; rejection flags overlap."
        ),
    }


def summarize(records):
    n = len(records)
    positives = [r for r in records if r["retrieval"]["complete"] is not None]
    return {
        "questions": n,
        "reference_answerable": len(positives),
        "evidence_complete_count": sum(r["retrieval"]["complete"] for r in positives),
        "evidence_complete": sum(r["retrieval"]["complete"] for r in positives) / len(positives)
        if positives
        else None,
        "evidence_recall": sum(r["retrieval"]["recall"] for r in positives) / len(positives)
        if positives
        else None,
        "mean_documents": sum(len(r["documents"]) for r in records) / n if n else None,
        "mean_source_characters": sum(r["retrieval"]["source_characters"] for r in records) / n
        if n
        else None,
    }


def run(root, output):
    verify(root, "formal-frozen.json")
    if output.exists():
        raise ValueError("choose a new supplemental output directory")
    output.mkdir(mode=0o700)
    semantic = root / "full-semantic-review"
    reviewed = json.loads((semantic / "summary.json").read_text())
    primary = set(reviewed["primary_question_ids"])
    pairs = json.loads((semantic / "pairs.json").read_text())
    labels = json.loads((semantic / "labels.json").read_text())
    judgments = {
        (r["model"], r["case_id"], r["arm"]): {
            "grade": labels[r["group"]]["judgment"],
            "citation": r["citation"],
        }
        for r in pairs
    }
    attempted = {p["case_id"] for p in pairs}
    cases = json.loads((root / "cases.json").read_text())
    untouched = {c["id"] for c in cases if c["id"] not in attempted}
    docs_list = json.loads((root / "corpus.json").read_text())
    docs = {d["id"]: d for d in docs_list}
    vector_ranks = json.loads((root / "vector-ranks.json").read_text())
    frozen = json.loads((root / "formal-retrieval.json").read_text())
    bykey = {(r["model"], r["id"], r["arm"]): r for r in frozen}
    raw = logical_rows(load_rows(root / "calls"))
    source_bm25, batches = SourceBM25(docs), data.batches(docs_list)
    output_rows, diagnostics, answer_changes, results = [], [], [], {}
    verified, ordering_mismatches = 0, []
    for model in MODELS:
        facts = graph_facts(raw, "formal_graph", model)
        graph = baseline.GraphIndex(facts)
        constructed = supplied_units({f["d"] for f in facts}, docs)
        model_diagnostics = []
        for case, vector_rank in zip(cases, vector_ranks, strict=True):
            ident, question = case["id"], case["question"]
            trace = trace_graph(graph, question)
            replay = baseline.contexts(vector_rank, trace["rank"][:6], docs)
            for arm in ARMS:
                saved = bykey[model, ident, arm]
                if saved["documents"] != replay[arm]:
                    ordering_mismatches.append(
                        {
                            "model": model,
                            "id": ident,
                            "arm": arm,
                            "saved": saved["documents"],
                            "replay": replay[arm],
                        }
                    )
                verified += 1
                if baseline.support_scores(case, saved["documents"], docs) != saved["retrieval"]:
                    raise ValueError("frozen evidence scores changed")
            cohort = "primary" if ident in primary else "unanswered" if ident in untouched else None
            if cohort is None:
                continue
            saved = {a: bykey[model, ident, a] for a in ARMS}
            variants = {
                "fill": fill_context(saved["U12"]["documents"], vector_rank, docs),
                "tie_fill": fill_context(
                    baseline.pack(
                        tie_rank(trace, source_bm25, question)[:6],
                        docs,
                        retained=saved["V6"]["documents"],
                    ),
                    vector_rank,
                    docs,
                ),
                "pool_rrf": pool_context(vector_rank, trace["rank"][:24], docs),
            }
            for name, chosen in variants.items():
                if not set(saved["V6"]["documents"]) <= set(chosen) or len(chosen) > 12:
                    raise ValueError("variant lost retained source or exceeded document cap")
                if len(json.dumps(baseline.source_units(chosen, docs), ensure_ascii=False)) > 12000:
                    raise ValueError("variant exceeded frozen source budget")
                output_rows.append(
                    {
                        "model": model,
                        "id": ident,
                        "cohort": cohort,
                        "variant": name,
                        "documents": chosen,
                        "retrieval": baseline.support_scores(case, chosen, docs),
                    }
                )
            if ident not in primary:
                continue
            units = {a: supplied_units(saved[a]["documents"], docs) for a in ARMS}
            gold = set(case["support"])
            missing = gold - units["V6"]
            reachable = supplied_units(trace["rank"], docs)
            raw_g6 = supplied_units(trace["rank"][:6], docs)
            stages = Counter()
            for u in sorted(missing):
                if u not in constructed:
                    stages["no_accepted_fact_on_support_document"] += 1
                elif u not in reachable:
                    stages["not_reached_from_question_seeds"] += 1
                elif u not in raw_g6:
                    stages["reached_but_below_graph_top6"] += 1
                elif u not in units["G6"]:
                    stages["graph_only_budget_omitted"] += 1
                elif u not in units["U12"]:
                    stages["union_budget_omitted"] += 1
                else:
                    stages["rescued"] += 1
            diag = {
                "model": model,
                "id": ident,
                "question": question,
                "dataset": case["dataset"],
                "missing_v6_gold_units": len(missing),
                "missing_stages": dict(stages),
                "seeds": trace["seeds"],
                "cap_hit": trace["cap_hit"],
                "visited_nodes": trace["visited_nodes"],
                "credited_edges": trace["credited_edges"],
                "reachable_documents": len(trace["rank"]),
                "top_score_ties": trace["top_score_ties"],
                "sixth_score_ties": trace["sixth_score_ties"],
                "same_u12_v6": saved["U12"]["documents"] == saved["V6"]["documents"],
                "additional_graph_documents": len(
                    set(saved["U12"]["documents"]) - set(saved["V6"]["documents"])
                ),
                "new_gold_units": sorted((units["U12"] - units["V6"]) & gold),
                "new_graph_nongold_documents": sum(
                    not (supplied_units([d], docs) & gold)
                    for d in set(saved["U12"]["documents"]) - set(saved["V6"]["documents"])
                ),
            }
            # Do not infer irrelevance from absence in the gold support set.
            model_diagnostics.append(diag)
            v, u = (judgments[model, ident, a] for a in ("V12", "U12"))
            change = (
                "win"
                if u["grade"] == "c" and v["grade"] != "c"
                else ("loss" if v["grade"] == "c" and u["grade"] != "c" else "tie")
            )
            answer_changes.append(
                {
                    "model": model,
                    "id": ident,
                    "question": question,
                    "dataset": case["dataset"],
                    "change": change,
                    "vector_grade": v["grade"],
                    "union_grade": u["grade"],
                    "vector_evidence": saved["V12"]["retrieval"],
                    "union_evidence": saved["U12"]["retrieval"],
                    "vector_citation": v["citation"],
                    "union_citation": u["citation"],
                    "gold_added_vs_v12": sorted((units["U12"] - units["V12"]) & gold),
                    "gold_lost_vs_v12": sorted((units["V12"] - units["U12"]) & gold),
                }
            )
        diagnostics.extend(model_diagnostics)
        model_rows = [r for r in output_rows if r["model"] == model]
        arms = {}
        for cohort, ids in (("primary", primary), ("unanswered", untouched)):
            arms[cohort] = {}
            for arm in ARMS:
                chosen = [
                    r for r in frozen if r["model"] == model and r["arm"] == arm and r["id"] in ids
                ]
                arms[cohort][arm] = summarize(chosen)
            for name in ("fill", "tie_fill", "pool_rrf"):
                chosen = [r for r in model_rows if r["variant"] == name and r["cohort"] == cohort]
                arms[cohort][name] = summarize(chosen)
        changes = [r for r in answer_changes if r["model"] == model]
        evidence_reader, citation_gaps = {}, {}
        for arm, field, citation in (
            ("V12", "vector_grade", "vector_citation"),
            ("U12", "union_grade", "union_citation"),
        ):
            evidence_key = "vector_evidence" if arm == "V12" else "union_evidence"
            evidence_reader[arm] = dict(
                Counter(
                    ("full" if c[evidence_key]["complete"] else "missing") + "/" + c[field]
                    for c in changes
                )
            )
            citation_gaps[arm] = {
                "evidence_incomplete": sum(not c[evidence_key]["complete"] for c in changes),
                "evidence_full_but_citation_incomplete": sum(
                    c[evidence_key]["complete"] and c[citation]["complete"] != 1 for c in changes
                ),
                "correct_full_evidence_but_citation_incomplete": sum(
                    c[field] == "c" and c[evidence_key]["complete"] and c[citation]["complete"] != 1
                    for c in changes
                ),
            }
        results[model] = {
            "construction": construction_audit(
                [r for r in raw if r["kind"] == "formal_graph" and r["model"] == model],
                batches,
                docs,
            ),
            "cohorts": arms,
            "missing_v6_gold_units_by_stage": dict(
                sorted(
                    Counter(
                        {
                            k: sum(d["missing_stages"].get(k, 0) for d in model_diagnostics)
                            for k in sorted(
                                {k for d in model_diagnostics for k in d["missing_stages"]}
                            )
                        }
                    ).items()
                )
            ),
            "traversal": {
                "queries_hitting_cap": sum(d["cap_hit"] for d in model_diagnostics),
                "no_seeds": sum(not d["seeds"] for d in model_diagnostics),
                "mean_sixth_score_ties": sum(d["sixth_score_ties"] for d in model_diagnostics)
                / len(primary),
                "queries_sixth_score_ties_gt6": sum(
                    d["sixth_score_ties"] > 6 for d in model_diagnostics
                ),
                "same_union_as_v6": sum(d["same_u12_v6"] for d in model_diagnostics),
                "additional_graph_documents": sum(
                    d["additional_graph_documents"] for d in model_diagnostics
                ),
                "new_graph_nongold_documents": sum(
                    d["new_graph_nongold_documents"] for d in model_diagnostics
                ),
            },
            "saved_answer_evidence_matrix": evidence_reader,
            "citation_gap_decomposition": citation_gaps,
            "saved_reader_wins_losses": dict(Counter(c["change"] for c in changes)),
        }
        print(
            json.dumps(
                {
                    "model": model,
                    "primary_retrieval_complete": {
                        a: arms["primary"][a]["evidence_complete_count"] for a in arms["primary"]
                    },
                    "replay_mismatches_so_far": len(ordering_mismatches),
                }
            ),
            flush=True,
        )
    if ordering_mismatches:
        save(output / "replay-mismatches.json", ordering_mismatches)
        raise ValueError(
            "trace did not reproduce frozen graph ordering; variants are not attributable"
        )
    summary = {
        "primary_questions": len(primary),
        "unanswered_questions": len(untouched),
        "baseline_plans_verified": verified,
        "baseline_ordering_mismatches": 0,
        "offline_variant_count": 3,
        "new_cloud_calls": 0,
        "new_answer_accuracy_measured": False,
        "models": results,
        "input_sha256": {
            str(p): file_hash(p)
            for p in [
                root / "formal-frozen.json",
                root / "cases.json",
                root / "corpus.json",
                root / "formal-retrieval.json",
                root / "vector-ranks.json",
                semantic / "summary.json",
                semantic / "labels.json",
                semantic / "question-quality.json",
                semantic / "pairs.json",
            ]
        },
    }
    save(output / "summary.json", summary)
    save(output / "variants.json", output_rows)
    save(output / "diagnostics.json", diagnostics)
    save(output / "answer-changes.json", answer_changes)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(".cache/dual-path-2026-10-20-v3a"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    run(args.root, args.output or args.root / "retrieval-audit")
