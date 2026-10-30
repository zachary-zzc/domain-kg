"""Descriptive and paired summaries; semantic model judgments are not gold labels."""

import argparse
from collections import Counter
from pathlib import Path

import numpy as np

from eval.source_role_eval import EXTRACTOR, JUDGE, digest
from eval.source_role_evidence import evidence_request
from eval.source_role_expand import ARMS, summarize, verify
from eval.source_role_normalize import normalize_evidence_judgment, normalize_judgments
from eval.source_role_run import Ledger, content, read, write_new


def scored_document(row, judgment, arm):
    references = {r["id"] for r in judgment["references"] if r["verdict"] == "supported"}
    facts = [f for f in judgment["facts"] if row["fact_mapping"][f["id"]]["arm"] == arm]
    covered = set()
    for fact in facts:
        if fact["verdict"] == "supported":
            covered.update(set(fact["matches"]) & references)
    return [
        sum(f["verdict"] == "supported" for f in facts),
        len(facts),
        len(covered),
        len(references),
    ]


def construction_stats(records, judgments):
    result = {}
    for arm in ARMS:
        rows = [
            scored_document(r, judgments[r["id"]], arm)
            for r in records
            if judgments.get(r["id"]) is not None
        ]
        counts = np.asarray(rows, dtype=int).sum(axis=0).tolist() if rows else [0] * 4
        supported, facts, covered, references = counts
        accepted = sum(len(r["arms"].get(arm, {}).get("facts", [])) for r in records)
        result[arm] = {
            "judged_documents": len(rows),
            "supported": supported,
            "judged_facts": facts,
            "accepted_facts": accepted,
            "unjudged_facts": accepted - facts,
            "covered": covered,
            "reference_facts": references,
            "precision": supported / facts if facts else None,
            "coverage": covered / references if references else None,
            "raw_proposals": sum(r["arms"].get(arm, {}).get("raw_count", 0) for r in records),
            "rejected_proposals": sum(
                len(r["arms"].get(arm, {}).get("rejections", [])) for r in records
            ),
            "verdicts": dict(
                Counter(
                    f["verdict"]
                    for r in records
                    if judgments.get(r["id"]) is not None
                    for f in judgments[r["id"]]["facts"]
                    if r["fact_mapping"][f["id"]]["arm"] == arm
                )
            ),
        }
    return result


def paired_intervals(records, judgments, first, second):
    rows = [r for r in records if judgments.get(r["id"]) is not None]
    if not rows:
        return None
    a = np.asarray([scored_document(r, judgments[r["id"]], first) for r in rows])
    b = np.asarray([scored_document(r, judgments[r["id"]], second) for r in rows])
    rng = np.random.default_rng(20261021)
    indexes = rng.integers(0, len(rows), size=(5000, len(rows)))
    aa, bb = a[indexes].sum(axis=1), b[indexes].sum(axis=1)
    output = {
        "first": first,
        "second": second,
        "documents": len(rows),
        "method": "paired document bootstrap, 5000 draws, seed 20261021",
    }
    for label, num, den in (("precision", 0, 1), ("coverage", 2, 3)):
        if not a[:, den].sum() or not b[:, den].sum():
            output[label] = None
            continue
        valid = (aa[:, den] > 0) & (bb[:, den] > 0)
        differences = bb[valid, num] / bb[valid, den] - aa[valid, num] / aa[valid, den]
        output[label] = {
            "second_minus_first": b[:, num].sum() / b[:, den].sum()
            - a[:, num].sum() / a[:, den].sum(),
            "percentile_95_interval": np.quantile(differences, [0.025, 0.975]).tolist(),
        }
    return output


def agreement(records, primary, secondary):
    confusion = Counter()
    details = []
    documents = 0
    for row in records:
        a, b = primary.get(row["id"]), secondary.get(row["id"])
        if a is None or b is None:
            continue
        documents += 1
        by_id = {f["id"]: f for f in b["facts"]}
        for fact in a["facts"]:
            other = by_id[fact["id"]]
            confusion[fact["verdict"] + " -> " + other["verdict"]] += 1
            if fact["verdict"] != other["verdict"]:
                mapping = row["fact_mapping"][fact["id"]]
                details.append(
                    {
                        "document": row["id"],
                        **mapping,
                        "fact": row["arms"][mapping["arm"]]["facts"][mapping["index"]],
                        "primary": fact,
                        "secondary": other,
                    }
                )
    total = sum(confusion.values())
    return {
        "documents": documents,
        "facts": total,
        "disagreements": len(details),
        "agreement": (total - len(details)) / total if total else None,
        "confusion": dict(confusion),
        "details": details,
    }


def evidence_stats(root, *, normalize=False):
    packets = read(root / "packets.json")
    results = {r["id"]: r for p in (root / "judgments").glob("*.json") if (r := read(p))}
    normalizations = {}
    if normalize:
        ledger = Ledger(root / "calls")
        for packet in packets:
            if packet["id"] not in results:
                continue
            for arm, evidence in packet["arms"].items():
                row = results[packet["id"]]["arms"][arm]
                payload = evidence_request(packet, arm, row["reader"])
                raw = read(ledger.paths("evidence:" + digest(payload))[1])
                try:
                    row["judgment"], changes = normalize_evidence_judgment(
                        content(raw),
                        {s["id"] for s in evidence["sources"]},
                        bool(evidence["paths"]),
                    )
                    row["errors"] = [e for e in row["errors"] if e != "judge_invalid_or_missing"]
                    if changes:
                        normalizations[packet["id"] + "/" + arm] = changes
                except ValueError:
                    pass
    stats = {}
    for arm in packets[0]["arms"]:
        complete = [r["arms"][arm] for r in results.values()]
        judged = [r["judgment"] for r in complete if r["judgment"] is not None]
        stats[arm] = {
            "planned_questions": len(packets),
            "completed_questions": len(complete),
            "valid_judgments": len(judged),
            "sufficiency": dict(Counter(j["sufficiency"] for j in judged)),
            "reader_status": dict(Counter(j["reader_status"] for j in judged)),
            "reference_status": dict(Counter(j["reference_status"] for j in judged)),
            "path_status": dict(Counter(j["path_status"] for j in judged)),
            "questions_with_paths": sum(bool(p["arms"][arm]["paths"]) for p in packets),
            "identical_to_rag": sum(p["arms"][arm] == p["arms"]["RAG"] for p in packets),
            "reference_complete": sum(
                p["diagnostics"][arm]["reference_coverage"]["complete"] == 1 for p in packets
            ),
            "errors": dict(Counter(e for r in complete for e in r["errors"])),
        }
    transitions = {}
    for arm in stats:
        transitions[arm] = [
            {
                "id": p["id"],
                "question": p["question"],
                "rag": results[p["id"]]["arms"]["RAG"]["judgment"],
                "candidate": results[p["id"]]["arms"][arm]["judgment"],
            }
            for p in packets
            if p["id"] in results and p["arms"][arm] != p["arms"]["RAG"]
        ]
    return {"stats": stats, "transitions": transitions, "normalizations": normalizations}


def blind_stats(root):
    packets = read(root / "packets.json")
    results = {r["id"]: r for p in (root / "blind-review").glob("*.json") if (r := read(p))}
    stats = {}
    for arm in packets[0]["arms"]:
        rows = [r["arms"][arm] for r in results.values()]
        judgments = [r["judgment"] for r in rows if r["judgment"] is not None]
        stats[arm] = {
            "planned_questions": len(packets),
            "completed_questions": len(rows),
            "valid_judgments": len(judgments),
            **{
                name: dict(Counter(j[name] for j in judgments))
                for name in ("question_status", "sufficiency", "reader_status", "path_status")
            },
            "errors": sum(r["error"] is not None for r in rows),
        }
    transitions = {
        arm: [
            {
                "id": p["id"],
                "question": p["question"],
                "rag": results[p["id"]]["arms"]["RAG"]["judgment"],
                "candidate": results[p["id"]]["arms"][arm]["judgment"],
            }
            for p in packets
            if p["id"] in results and p["arms"][arm] != p["arms"]["RAG"]
        ]
        for arm in stats
    }
    return {"stats": stats, "transitions": transitions}


def analyze(root, evidence):
    frozen, docs = verify(root)
    records = [read(root / "documents" / (d["id"] + ".json")) for d in docs]
    original = {r["id"]: r.get("judgments") for r in records}

    def judgments(directory):
        return {r["id"]: r.get("judgments") for p in directory.glob("*.json") if (r := read(p))}

    role = judgments(root / "role-audit" / EXTRACTOR)
    secondary = judgments(root / "role-audit" / JUDGE)
    cross = judgments(root / "cross-review")
    ledger = Ledger(root / "calls")

    def recovered(label):
        values, adjustments, errors = {}, {}, {}
        for row in records:
            path = ledger.paths(label(row["id"]))[1]
            if not path.exists():
                continue
            try:
                value, changes = normalize_judgments(
                    content(read(path)),
                    set(row["fact_mapping"]),
                    {r["id"] for r in row["references"]},
                )
                values[row["id"]] = value
                if changes:
                    adjustments[row["id"]] = changes
            except ValueError:
                values[row["id"]] = None
                errors[row["id"]] = "invalid_or_missing"
        return values, {"adjustments": adjustments, "errors": errors}

    normalized, role_diagnostics = recovered(lambda id: "role-audit:" + EXTRACTOR + ":" + id)
    normalized_second, secondary_diagnostics = recovered(
        lambda id: "role-audit:" + JUDGE + ":" + id
    )
    directories = [Path(frozen["pilot"]) / "calls", root / "calls", evidence / "calls"]
    launches = [read(p) for d in directories for p in d.glob("*.launch.json")]
    results = {read(p)["label"]: read(p) for d in directories for p in d.glob("*.result.json")}
    unknown = [r for r in launches if results.get(r["label"], {}).get("usage") is None]
    actual = sum(r["actual_rmb"] or 0 for r in results.values())
    return {
        "original": summarize(root),
        "role_audit": construction_stats(records, role),
        "role_audit_normalized": construction_stats(records, normalized),
        "role_audit_normalization": role_diagnostics,
        "role_secondary_normalization": secondary_diagnostics,
        "original_cross_model": agreement(records, original, cross),
        "role_cross_model": agreement(records, role, secondary),
        "role_cross_model_normalized": agreement(records, normalized, normalized_second),
        "original_vs_role_audit": agreement(records, original, role),
        "paired_intervals": [
            paired_intervals(records, normalized, a, b)
            for a, b in [
                ("baseline", "coverage"),
                ("coverage", "source_repair"),
                ("source_repair", "context_repair"),
            ]
        ],
        "evidence": evidence_stats(evidence),
        "evidence_normalized": evidence_stats(evidence, normalize=True),
        "evidence_blind": blind_stats(evidence),
        "cost": {
            "attempts": len(launches),
            "known_list_price_rmb": actual,
            "unknown_usage_attempts": len(unknown),
            "known_plus_unknown_reserve_rmb": actual + sum(r["reserved_rmb"] for r in unknown),
            "all_reservations_rmb": sum(r["reserved_rmb"] for r in launches),
            "stages": dict(
                Counter(
                    r["label"].split(":")[0]
                    if not r["label"].startswith("d-")
                    else r["label"].split(":")[-1]
                    for r in launches
                )
            ),
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    write_new(args.output, analyze(args.root, args.evidence))


if __name__ == "__main__":
    main()
