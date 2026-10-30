"""Compare cloud answer models on frozen V3, V6 and append-only graph evidence."""

import argparse
import asyncio
import json
import shutil
import time
from pathlib import Path

from eval.cloud_calls import CloudCalls, checked_endpoint
from eval.fusion_pilot import graph_recall
from eval.fusion_run import CODE as PARENT_CODE
from eval.holdout_pilot import audited_score, check_files, dense_rank, fingerprint, score, sha256
from eval.hybrid_run import answer_request
from eval.no_filter import contexts
from eval.recursive_run import load_sources
from scripts.native_model import ROOT

HOME = ROOT / ".cache/cloud-model-comparison"
PARENT = ROOT / ".cache/dual-retrieve-filter/heldout"
MODELS = ("qwen3.8-flash", "qwen3.7-plus")
ARMS = ("V3", "V6", "U")
ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1"
CODE = (
    *PARENT_CODE,
    "eval/fusion_run.py",
    "eval/fusion_cases.json",
    "eval/no_filter.py",
    "eval/cloud_calls.py",
    "eval/cloud_compare.py",
    "docs/superpowers/plans/2026-10-19-cloud-model-comparison.md",
)


def read(path):
    return json.loads(path.read_bytes())


def save(name, value):
    path = HOME / name
    path.parent.mkdir(mode=0o700, exist_ok=True, parents=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def request_for(model, query, evidence):
    original = answer_request(model, query, evidence)
    return {
        "model": model,
        "messages": [
            {
                **original["messages"][0],
                "content": original["messages"][0]["content"]
                + ' Return JSON only with exactly two fields: "answer" (string) and "citations" '
                "(array of at most three supplied evidence IDs).",
            },
            original["messages"][1],
        ],
        "temperature": 0,
        "seed": 19,
        "enable_thinking": False,
        "max_tokens": 192,
        "stream": False,
        "response_format": {"type": "json_object"},
    }


def prepare():
    if HOME.exists():
        raise RuntimeError("Cloud experiment already exists; no reset or answer-driven tuning")
    parent = read(PARENT / "manifest.json")
    check_files(ROOT, parent["files"])
    check_files(PARENT, parent["artifacts"])
    _, source_hashes = load_sources()
    if source_hashes != parent["source_hashes"]:
        raise RuntimeError("Frozen public sources changed")
    HOME.mkdir(mode=0o700)
    for name in ("corpus.json", "embeddings.json", "graph.json", "cases.json"):
        shutil.copyfile(PARENT / name, HOME / name)
    chunks, vectors, graph = (
        read(HOME / n) for n in ("corpus.json", "embeddings.json", "graph.json")
    )
    questions = read(HOME / "cases.json")["queries"]
    matrix = [vectors["chunk/" + c["id"]] for c in chunks]
    texts = {c["id"]: c["text"] for c in chunks}
    retrievals, requests = {}, []
    for q in questions:
        dense = dense_rank(chunks, matrix, vectors["query/" + q["id"]])
        graph_ids = graph_recall(chunks, graph, q["query"])["selected"]
        retrievals[q["id"]] = contexts(chunks, dense, graph_ids, q["query"])
        for model in MODELS:
            for arm in ARMS:
                selected = retrievals[q["id"]][arm]
                payload = request_for(
                    model, q["query"], [{"id": key, "text": texts[key]} for key in selected]
                )
                requests.append(
                    {
                        "id": q["id"],
                        "split": q["split"],
                        "arm": arm,
                        "model": model,
                        "selected": selected,
                        "request": payload,
                        "request_sha256": fingerprint(payload),
                    }
                )
    save("retrievals.json", retrievals)
    save("requests.json", requests)
    artifacts = {p.name: sha256(p) for p in HOME.glob("*.json")}
    files = {name: sha256(ROOT / name) for name in dict.fromkeys(CODE)}
    for name in files:
        path = HOME / "frozen-files" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, path)
    save(
        "manifest.json",
        {
            "status": "frozen_cloud_diagnostic",
            "files": files,
            "artifacts": artifacts,
            "source_hashes": source_hashes,
            "endpoint": checked_endpoint(ENDPOINT),
            "models": list(MODELS),
            "questions": len(questions),
            "planned_answers": len(requests),
            "independent_acceptance": False,
        },
    )
    print(
        json.dumps(
            {
                "questions": len(questions),
                "logical_answers": len(requests),
                "unique_requests": len({r["request_sha256"] for r in requests}),
                "coverage": {
                    arm: sum(
                        set(q["expected"]) <= set(retrievals[q["id"]][arm])
                        for q in questions
                        if q["expected"]
                    )
                    for arm in ARMS
                },
            }
        ),
        flush=True,
    )


def frozen():
    manifest = read(HOME / "manifest.json")
    check_files(ROOT, manifest["files"])
    check_files(HOME, manifest["artifacts"])
    _, hashes = load_sources()
    if hashes != manifest["source_hashes"]:
        raise RuntimeError("Frozen public source changed")
    return manifest


async def run(secret_file):
    manifest = frozen()
    if (HOME / "calls").exists():
        raise RuntimeError("Already attempted; no silent retry")
    key = secret_file.read_text().strip()
    if not key or secret_file.stat().st_mode & 0o077:
        raise ValueError("Credential file must be nonempty and mode 0600")
    calls = CloudCalls(manifest["endpoint"], key, HOME / "calls")
    requests = read(HOME / "requests.json")
    questions = read(HOME / "cases.json")["queries"]
    by_id = {q["id"]: q for q in questions}
    pending = {(r["id"], r["model"], r["arm"]): r for r in requests}
    rows, cache, failure = [], {}, None
    started = time.monotonic()
    try:
        for index, q in enumerate(questions):
            models = MODELS[index % 2 :] + MODELS[: index % 2]
            arms = ARMS[index % 3 :] + ARMS[: index % 3]
            for model in models:
                for arm in arms:
                    selected = pending[q["id"], model, arm]
                    row = {k: v for k, v in selected.items() if k != "request"}
                    hashed = row["request_sha256"]
                    row["cached_identical_request"] = hashed in cache
                    if hashed in cache:
                        outcome = cache[hashed]
                    else:
                        outcome = await calls.invoke(
                            q["id"] + "/" + model + "/" + arm, selected["request"], row["selected"]
                        )
                        if not outcome["error"]:
                            cache[hashed] = outcome
                    row.update(outcome)
                    row["metrics"] = score(by_id[row["id"]], row["selected"], row["answer"])
                    rows.append(row)
                    save("answers.json", rows)
                    print(
                        json.dumps(
                            {
                                "model": model,
                                "id": row["id"],
                                "arm": arm,
                                "error": row["error"],
                                "cached": row["cached_identical_request"],
                                "seconds": row["seconds"],
                            }
                        ),
                        flush=True,
                    )
                    if calls.halted:
                        raise RuntimeError("Predeclared cloud stop; last failure retained")
    except Exception as error:
        failure = type(error).__name__ + ": " + str(error).replace(key, "[REDACTED]")
    finally:
        await calls.close()
        save("answers.json", rows)
        save(
            "stage.json",
            {
                "evaluation_complete": len(rows) == 144 and not calls.failures and not failure,
                "failure": failure,
                "runtime_failures": calls.failures,
                "logical_answers": len(rows),
                "http_requests": len(calls.records),
                "seconds": round(time.monotonic() - started, 3),
                "request_seconds": round(sum(r["seconds"] for r in calls.records), 3),
                "usage": {
                    k: sum(r.get("usage", {}).get(k, 0) for r in calls.records)
                    for k in ("prompt_tokens", "completion_tokens")
                },
                "failed_usage_unknown": sum("usage" not in r for r in calls.records),
            },
        )
    print(json.dumps(read(HOME / "stage.json")), flush=True)


def audit():
    frozen()
    rows, decisions = read(HOME / "answers.json"), read(HOME / "audit.json")
    by_key = {(d["id"], d["model"], d["arm"]): d for d in decisions}
    if len(decisions) != len(rows) or len(by_key) != len(rows):
        raise ValueError("Missing or duplicate semantic decisions")
    questions = read(HOME / "cases.json")["queries"]
    by_id = {q["id"]: q for q in questions}
    for row in rows:
        row["metrics"] = audited_score(
            by_id[row["id"]], row, by_key[row["id"], row["model"], row["arm"]]
        )
    save("answers-audited.json", rows)
    summary = {}
    for model in MODELS:
        for arm in ARMS:
            selected = [r for r in rows if r["model"] == model and r["arm"] == arm]
            summary[model + "/" + arm] = {
                "questions": 24,
                "answerable": 20,
                "completed": sum(r["answer"] is not None and not r["error"] for r in selected),
                "answers_correct": sum(r["metrics"]["answer_correct"] for r in selected),
                "citations_complete": sum(
                    bool(r["metrics"]["citation_complete"]) for r in selected
                ),
                "joint_supported_correct": sum(r["metrics"]["supported_correct"] for r in selected),
            }
    save("summary.json", summary)
    save(
        "decision.json",
        {
            "qualified": False,
            "independent_acceptance": False,
            "reason": "Controlled diagnostic, not independent acceptance or semantic KG extraction",
            "full_pdf_construction_started": False,
        },
    )
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("prepare", "run", "audit"))
    parser.add_argument("--secret-file", type=Path)
    args = parser.parse_args()
    if args.stage == "prepare":
        prepare()
    elif args.stage == "run":
        asyncio.run(run(args.secret_file))
    else:
        audit()
