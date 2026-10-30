"""Independent post-run audit and grouped metrics for the frozen dual-path run.

Primary answer metrics require complete coverage in their subgroup. Global
unanswerable cases retain their original local-context labels and are diagnostic.
"""

import argparse
import json
import math
from collections import Counter
from pathlib import Path

from eval import dual_path_data as data
from eval import dual_path_retrieval as retrieval
from eval.dual_path_run import ARMS, load_rows, parse_answer, response_content, verify
from eval.dual_path_transport import TransportBudget, logical_rows
from eval.public_qa_data import MODELS, fingerprint
from eval.public_qa_metrics import score_case
from eval.public_qa_prepare import file_hash, save


def wilson(correct, n):
    if not n:
        return None
    z = 1.959963984540054
    p, denominator = correct / n, 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0, center - radius), min(1, center + radius)


def paired(vector, union):
    ids = sorted(set(vector) & set(union))
    win = sum(union[i] == 1 and vector[i] == 0 for i in ids)
    loss = sum(union[i] == 0 and vector[i] == 1 for i in ids)
    discordant = win + loss
    pvalue = (
        min(1, 2 * sum(math.comb(discordant, k) for k in range(min(win, loss) + 1)) / 2**discordant)
        if discordant
        else 1
    )
    return {
        "paired": len(ids),
        "union_only_correct": win,
        "vector_only_correct": loss,
        "delta": (win - loss) / len(ids) if ids else None,
        "mcnemar_exact_p_unadjusted": pvalue,
    }


def common_cohort(cases, results, models, arms):
    """Post-hoc coverage-only intersection; model errors remain in denominators."""
    return [c["id"] for c in cases if all((m, c["id"], a) in results for m in models for a in arms)]


def group_scores(cases, records, rows):
    n = len(records)
    answered = [r for r in records if r["id"] in rows]
    answerable = [r for r in records if cases[r["id"]]["answerable"]]
    noanswer = [r for r in records if not cases[r["id"]]["answerable"]]
    available = [r for r in answerable if r["id"] in rows]
    noavailable = [r for r in noanswer if r["id"] in rows]

    def metric(members, name):
        if not members or any(r["id"] not in rows for r in members):
            return None
        return sum(rows[r["id"]].get("scores", {}).get(name, 0) for r in members) / len(members)

    correct = sum(rows[r["id"]].get("scores", {}).get("em", 0) for r in available)
    return {
        "planned": n,
        "attempted": len(answered),
        "coverage": len(answered) / n if n else 0,
        "answerable_planned": len(answerable),
        "answerable_attempted": len(available),
        "noanswer_planned": len(noanswer),
        "noanswer_attempted": len(noavailable),
        "all_em_original_labels": metric(records, "em"),
        "answerable_em": metric(answerable, "em"),
        "answerable_f1": metric(answerable, "f1"),
        "answerable_em_wilson95": wilson(correct, len(available))
        if len(available) == len(answerable)
        else None,
        "citation_complete": metric(answerable, "citation_complete"),
        "citation_precision": metric(answerable, "citation_precision"),
        "citation_recall": metric(answerable, "citation_recall"),
        "noanswer_accuracy": metric(noanswer, "refusal_correct"),
        "protocol_ok": metric(records, "protocol_ok"),
        "retrieval_complete": sum(r["retrieval"]["complete"] or 0 for r in answerable)
        / len(answerable)
        if answerable
        else None,
        "retrieval_recall": sum(r["retrieval"]["recall"] or 0 for r in answerable) / len(answerable)
        if answerable
        else None,
        "mean_seconds": sum(rows[r["id"]]["seconds"] for r in answered) / len(answered)
        if answered
        else None,
        "errors": dict(
            Counter(rows[r["id"]]["error"] for r in answered if rows[r["id"]].get("error"))
        ),
    }


def analyze(root):
    verify(root, "formal-frozen.json")
    cases_list = json.loads((root / "cases.json").read_text())
    cases = {c["id"]: c for c in cases_list}
    docs = json.loads((root / "corpus.json").read_text())
    lookup = {d["id"]: d for d in docs}
    records = json.loads((root / "formal-retrieval.json").read_text())
    all_rows = load_rows(root / "calls")
    selected_rows = logical_rows(all_rows)
    calls = {(r["kind"], r["model"], r["label"]): r for r in selected_rows}
    identities = {r["identity"] for r in all_rows}
    if len(identities) != len(all_rows):
        raise ValueError("duplicate call identity")
    budget = TransportBudget()
    for r in all_rows + json.loads((root / "prior-usage.json").read_text()):
        budget.add(r["model"], r.get("usage"))
    results, verified_requests, verified_scores = {}, 0, 0
    record_index = {}
    for record in records:
        ident, model, arm = record["id"], record["model"], record["arm"]
        key = model, ident, arm
        if key in record_index:
            raise ValueError("duplicate retrieval pair")
        record_index[key] = record
        c = cases[ident]
        if any(d not in lookup for d in record["documents"]):
            raise ValueError("retrieval crossed corpus")
        if retrieval.support_scores(c, record["documents"], lookup) != record["retrieval"]:
            raise ValueError("retrieval score mismatch")
        row = calls.get(("formal_answer", model, ident + "|" + arm))
        if row is None:
            continue
        sources = retrieval.source_units(record["documents"], lookup)
        request = data.answer_request(model, c, sources)
        if fingerprint(request) != row["request_sha256"]:
            raise ValueError("reader request hash mismatch")
        verified_requests += 1
        supplied_case = {
            **c,
            "contexts": sources,
            "wire_ids": {f"e{i}": s["id"] for i, s in enumerate(sources)},
        }
        content, error, _ = response_content(
            {
                "response": row["raw"],
                "error": row["error"] if row["error"] != "invalid_answer_protocol" else None,
            }
        )
        answer = parse_answer(content, supplied_case)[0] if error is None else None
        scores = score_case(c, answer)
        if scores != row.get("scores", score_case(c, None)):
            raise ValueError("independent score mismatch")
        verified_scores += 1
        results[key] = {**row, "scores": scores}
    for model, ident, arm in record_index:
        if arm == "U12":
            baseline = record_index[model, ident, "V6"]["documents"]
            if not set(baseline) <= set(record_index[model, ident, arm]["documents"]):
                raise ValueError("fusion lost baseline evidence")
    construction, groups, comparisons = {}, {}, {}
    cohort = common_cohort(cases_list, results, MODELS, ARMS)
    cohort_set = set(cohort)
    cohort_members = [c for c in cases_list if c["id"] in cohort_set]
    cohort_groups, cohort_comparisons = {}, {}
    batches = data.batches(docs)
    for model in MODELS:
        graph_rows = [
            r for r in selected_rows if r["kind"] == "formal_graph" and r["model"] == model
        ]
        facts = [f for r in graph_rows if r.get("graph") for f in r["graph"]["facts"]]
        for row in graph_rows:
            batch = batches[int(row["label"])]
            if row["request_sha256"] != fingerprint(data.extraction_request(model, batch)):
                raise ValueError("builder request hash mismatch")
            verified_requests += 1
            if row.get("graph"):
                raw = row["raw"]["choices"][0]["message"]["content"]
                if data.parse_graph(raw, batch) != row["graph"]:
                    raise ValueError("builder parsing mismatch")
        construction[model] = {
            "planned_calls": len(batches),
            "attempted_calls": len(graph_rows),
            "protocol_usable_calls": sum(not r["error"] for r in graph_rows),
            "batches_with_grounded_facts": sum(
                bool((r.get("graph") or {}).get("facts")) for r in graph_rows
            ),
            "grounded_facts": len(facts),
            "paragraphs_with_facts": len({f["d"] for f in facts}),
            "paragraph_count": len(docs),
            "rejected_records": sum((r.get("graph") or {}).get("rejected", 0) for r in graph_rows),
            "errors": dict(Counter(r["error"] for r in graph_rows if r["error"])),
            "semantic_precision_recall": None,
            "note": (
                "Public QA lacks exhaustive relation gold; "
                "source grounding is not semantic accuracy."
            ),
        }
        groups[model] = {}
        partitions = {"all": cases_list}
        for field in ("dataset", "difficulty", "stratum"):
            for value in sorted({c[field] for c in cases_list}):
                partitions[field + ":" + value] = [c for c in cases_list if c[field] == value]
        for arm in ARMS:
            groups[model][arm] = {}
            for name, members in partitions.items():
                ids = {c["id"] for c in members}
                selected = [
                    r for r in records if r["model"] == model and r["arm"] == arm and r["id"] in ids
                ]
                rs = {ident: r for (m, ident, a), r in results.items() if m == model and a == arm}
                groups[model][arm][name] = group_scores(cases, selected, rs)

        def answers(arm, model=model):
            return {
                ident: r["scores"]["em"]
                for (m, ident, a), r in results.items()
                if m == model and a == arm and cases[ident]["answerable"]
            }

        comparisons[model] = {
            "U12_vs_V6": paired(answers("V6"), answers("U12")),
            "U12_vs_V12": paired(answers("V12"), answers("U12")),
        }
        cohort_groups[model] = {}
        cohort_partitions = {"all": cohort_members}
        for field in ("dataset", "difficulty", "stratum"):
            for value in sorted({c[field] for c in cohort_members}):
                cohort_partitions[field + ":" + value] = [
                    c for c in cohort_members if c[field] == value
                ]
        for arm in ARMS:
            cohort_groups[model][arm] = {}
            for name, members in cohort_partitions.items():
                ids = {c["id"] for c in members}
                selected = [
                    r for r in records if r["model"] == model and r["arm"] == arm and r["id"] in ids
                ]
                rs = {ident: r for (m, ident, a), r in results.items() if m == model and a == arm}
                cohort_groups[model][arm][name] = group_scores(cases, selected, rs)
        cohort_comparisons[model] = {
            "U12_vs_V6": paired(
                {i: s for i, s in answers("V6").items() if i in cohort_set},
                {i: s for i, s in answers("U12").items() if i in cohort_set},
            ),
            "U12_vs_V12": paired(
                {i: s for i, s in answers("V12").items() if i in cohort_set},
                {i: s for i, s in answers("U12").items() if i in cohort_set},
            ),
        }
    output = {
        "budget": vars(budget),
        "construction": construction,
        "groups": groups,
        "paired_comparisons": comparisons,
        "common_cohort": {
            "selection": (
                "Post-hoc complete-call intersection across every model and arm; "
                "errors count as attempted. No correctness filtering."
            ),
            "ids": cohort,
            "questions": len(cohort),
            "answerable": sum(c["answerable"] for c in cohort_members),
            "dataset_counts": dict(Counter(c["dataset"] for c in cohort_members)),
            "groups": cohort_groups,
            "paired_comparisons": cohort_comparisons,
        },
        "verified_requests": verified_requests,
        "verified_answer_scores": verified_scores,
        "formal_answer_planned": len(records),
        "formal_answer_attempted": len(results),
        "full_pdf_built": False,
        "production_integrated": False,
        "analysis_code_sha256": file_hash(Path(__file__)),
        "unanswerable_warning": (
            "Labels were defined with local contexts; global-corpus abstention "
            "is diagnostic, not a validated unanswerability benchmark."
        ),
    }
    save(root / "verified-analysis.json", output)
    print(
        json.dumps(
            {
                k: output[k]
                for k in (
                    "budget",
                    "verified_requests",
                    "verified_answer_scores",
                    "formal_answer_planned",
                    "formal_answer_attempted",
                )
            }
        ),
        flush=True,
    )
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    analyze(parser.parse_args().root)
