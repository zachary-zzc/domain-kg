"""Frozen dense/source-graph comparison; independent answer/citation metrics."""

import hashlib
import json
import math
from pathlib import Path

import numpy as np

from eval.hybrid_audit import assess
from eval.hybrid_paths import walk
from eval.hybrid_pilot import fit_context


def sha256(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def check_files(root, files):
    for name, expected in files.items():
        if sha256(root / name) != expected:
            raise RuntimeError("Frozen input changed: " + name)


def dense_rank(chunks, embeddings, query_vector):
    matrix, query = np.asarray(embeddings, dtype=float), np.asarray(query_vector, dtype=float)
    if (
        matrix.ndim != 2
        or matrix.shape[0] != len(chunks)
        or query.shape != matrix.shape[1:]
        or not np.isfinite(matrix).all()
        or not np.isfinite(query).all()
        or not np.linalg.norm(query)
        or np.any(np.linalg.norm(matrix, axis=1) == 0)
    ):
        raise ValueError("Invalid dense vectors")
    scores = matrix @ query / (np.linalg.norm(matrix, axis=1) * np.linalg.norm(query))
    return [chunks[i]["id"] for i in np.argsort(-scores, kind="stable")]


def select(chunks, embeddings, query_vector, arm, graph):
    ranking = dense_rank(chunks, embeddings, query_vector)
    if arm == "D":
        ranking = walk(graph, ranking[0], ranking, steps=2)
    elif arm != "V":
        raise ValueError("Unknown arm")
    return fit_context(ranking, chunks, top_k=3, max_chars=1800)


def score(question, selected, answer):
    measured = assess(question, selected, answer)
    complete = (
        bool(
            measured["citation_valid"]
            and set(question["expected"]) <= set(selected)
            and set(question["expected"]) <= set((answer or {}).get("citations", []))
        )
        if question["expected"]
        else None
    )
    return {
        **measured,
        "answer_correct": measured["answer_match"],
        "citation_complete": complete,
        "audited": False,
    }


def audited_score(question, row, audit):
    if (
        audit.get("answer_sha256") != fingerprint(row.get("answer"))
        or type(audit.get("answer_correct")) is not bool
        or not audit.get("reason", "").strip()
        or (row.get("error") and audit["answer_correct"])
    ):
        raise ValueError("Invalid or stale semantic audit")
    result = score(question, row["selected"], row.get("answer"))
    result["answer_correct"] = audit["answer_correct"]
    result["audited"] = True
    result["supported_correct"] = bool(
        audit["answer_correct"]
        and (result["citation_complete"] if question["expected"] else result["citation_valid"])
    )
    return result


def wilson(successes, total):
    if not total:
        return None
    p, z = successes / total, 1.959963984540054
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    width = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [center - width, center + width]


def summaries(records, questions):
    allowed = {(q["id"], arm) for q in questions for arm in ("V", "D")}
    rows = {}
    for row in records:
        key = row["id"], row["arm"]
        if key not in allowed or key in rows:
            raise ValueError("Duplicate or out-of-scope answer")
        rows[key] = row
    result = {}
    splits = ["all"] + sorted({q["split"] for q in questions})
    for arm in ("V", "D"):
        for split in splits:
            expected = [q for q in questions if split == "all" or q["split"] == split]
            metrics = []
            completed = 0
            for q in expected:
                row = rows.get((q["id"], arm), {})
                metrics.append(row.get("metrics") or score(q, row.get("selected", []), None))
                completed += bool(row.get("answer") is not None and not row.get("error"))
            citations = [m for m in metrics if m["citation_complete"] is not None]
            correct = sum(m["answer_correct"] for m in metrics)
            complete = sum(m["citation_complete"] for m in citations)
            result[arm + "/" + split] = {
                "questions": len(expected),
                "completed": completed,
                "failed_or_missing": len(expected) - completed,
                "audited": sum(m["audited"] for m in metrics),
                "answer_correct": correct,
                "answer_accuracy": correct / len(expected),
                "answer_accuracy_wilson95": wilson(correct, len(expected)),
                "label_matches": sum(m["answer_match"] for m in metrics),
                "citation_questions": len(citations),
                "citation_complete": complete,
                "citation_completeness": complete / len(citations) if citations else None,
                "citation_completeness_wilson95": wilson(complete, len(citations)),
                "supported_correct": sum(m["supported_correct"] for m in metrics),
                "complete_retrieval": sum(m["complete_support"] is True for m in metrics),
                "citation_valid": sum(m["citation_valid"] for m in metrics),
            }
    return result


def qualifies(stats):
    v, d = stats["V/all"], stats["D/all"]
    n, c = d["questions"], d["citation_questions"]
    return bool(
        n > 0
        and c > 0
        and v["questions"] == n
        and all(s["completed"] == n and s["audited"] == n for s in (v, d))
        and 100 * d["answer_correct"] >= 85 * n
        and 100 * d["citation_complete"] >= 90 * c
        and d["answer_correct"] >= v["answer_correct"]
    )
