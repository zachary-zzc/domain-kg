"""Frozen offline three-context diagnostic, separately owned 4B / 7B models."""

import argparse
import asyncio
import json
import shutil
import time
from types import SimpleNamespace

from domain_kg.providers.profiles import ProviderConnection
from eval.fusion_pilot import graph_recall
from eval.fusion_run import CODE as PARENT_CODE
from eval.holdout_pilot import audited_score, check_files, dense_rank, fingerprint, score, sha256
from eval.hybrid_run import answer_request
from eval.no_filter import contexts
from eval.recursive_run import load_sources
from eval.recursive_runtime import LocalCalls
from eval.seven_b_runtime import seven_b_model
from scripts.native_model import CACHE, ROOT, model

HOME = ROOT / ".cache/no-filter-7b"
PARENT = ROOT / ".cache/dual-retrieve-filter/heldout"
IDS = (
    "u07",
    "u08",
    "u09",
    "u10",
    "three_hop1",
    "three_hop2",
    "three_hop3",
    "three_hop4",
    "two_hop1",
    "two_hop3",
    "z1",
    "z3",
)
ARMS = ("V3", "V6", "U")
CODE = (
    *PARENT_CODE,
    "eval/fusion_run.py",
    "eval/fusion_cases.json",
    "eval/no_filter.py",
    "eval/no_filter_run.py",
    "eval/seven_b_runtime.py",
    "eval/7b-model.json",
    "docs/superpowers/plans/2026-10-19-no-filter-7b.md",
)


def read(path):
    return json.loads(path.read_bytes())


def save(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def prepare():
    if HOME.exists():
        raise RuntimeError("Experiment already exists; no reset or answer-driven tuning")
    previous = read(PARENT / "manifest.json")
    check_files(ROOT, previous["files"])
    check_files(PARENT, previous["artifacts"])
    _, hashes = load_sources()
    if hashes != previous["source_hashes"]:
        raise RuntimeError("Original sources changed")
    HOME.mkdir(mode=0o700)
    for name in ("corpus.json", "embeddings.json", "graph.json"):
        shutil.copyfile(PARENT / name, HOME / name)
    questions = [q for q in read(PARENT / "cases.json")["queries"] if q["id"] in IDS]
    if len(questions) != 12:
        raise RuntimeError("Wrong question cohort")
    save(HOME / "questions.json", questions)
    chunks, vectors, graph = (
        read(HOME / name) for name in ("corpus.json", "embeddings.json", "graph.json")
    )
    matrix = [vectors["chunk/" + c["id"]] for c in chunks]
    retrievals = {}
    for q in questions:
        dense = dense_rank(chunks, matrix, vectors["query/" + q["id"]])
        recalled = graph_recall(chunks, graph, q["query"])
        retrievals[q["id"]] = {
            **contexts(chunks, dense, recalled["selected"], q["query"]),
            "vector_ranking": dense,
            "graph_recall": recalled,
        }
    save(HOME / "retrievals.json", retrievals)
    shutil.copyfile(ROOT / "config/medical-model.approved.json", HOME / "4b-model.json")
    shutil.copyfile(ROOT / "eval/7b-model.json", HOME / "7b-model.json")
    artifacts = {p.name: sha256(p) for p in HOME.glob("*.json")}
    files = {name: sha256(ROOT / name) for name in dict.fromkeys(CODE)}
    for name in files:
        target = HOME / "frozen-files" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    save(
        HOME / "manifest.json",
        {
            "status": "frozen_diagnostic_not_independent_acceptance",
            "files": files,
            "artifacts": artifacts,
            "source_hashes": hashes,
            "runtime_sha256": sha256(ROOT / "/opt/homebrew/bin/llama-server"),
            "cohort": list(IDS),
            "planned_answers_per_model": 36,
            "parent_manifest_sha256": sha256(PARENT / "manifest.json"),
        },
    )
    print(
        json.dumps(
            {
                "prepared": True,
                "questions": len(questions),
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
        raise RuntimeError("Frozen original changed")
    if sha256(ROOT / "/opt/homebrew/bin/llama-server") != manifest["runtime_sha256"]:
        raise RuntimeError("Native runtime changed")
    return manifest


async def run(size):
    frozen()
    directory = HOME / size
    if directory.exists():
        raise RuntimeError("Model stage already exists; no rerun")
    directory.mkdir(mode=0o700)
    (directory / "calls").mkdir(mode=0o700)
    settings = read(HOME / (size + "-model.json"))
    questions = read(HOME / "questions.json")
    retrievals = read(HOME / "retrievals.json")
    chunks = {c["id"]: c for c in read(HOME / "corpus.json")}
    manager = (
        seven_b_model(HOME / "7b-model.json", directory)
        if size == "7b"
        else model("chat", manifest_path=HOME / "4b-model.json", log_directory=directory)
    )
    rows, cache, calls, guard, failure, errors = [], {}, None, None, None, 0
    tick = time.monotonic()
    try:
        async with manager as guard:
            calls = LocalCalls(
                SimpleNamespace(extraction_model=settings["served_model_name"]),
                ProviderConnection(
                    ref="owned-local",
                    endpoint=settings["endpoint"],
                    api_key=(CACHE / "llm-api-key").read_text().strip(),
                ),
                guard,
                directory / "calls",
            )
            calls.limits.max_calls = 36
            for index, q in enumerate(questions):
                arms = ARMS[index % 3 :] + ARMS[: index % 3]
                for arm in arms:
                    selected = retrievals[q["id"]][arm]
                    request = answer_request(
                        settings["served_model_name"],
                        q["query"],
                        [{"id": key, "text": chunks[key]["text"]} for key in selected],
                    )
                    key = fingerprint(request)
                    row = {
                        "id": q["id"],
                        "split": q["split"],
                        "arm": arm,
                        "model": size,
                        "selected": selected,
                        "request_sha256": key,
                        "cached_identical_request": key in cache,
                    }
                    if key in cache:
                        row.update(cache[key])
                    else:
                        calls.case_id = q["id"] + "/" + arm
                        start = time.monotonic()
                        try:
                            answer = await calls.invoke("evidence_answer", request)
                            row.update(answer=answer, error=None)
                        except Exception as error:
                            errors += 1
                            row.update(answer=None, error=type(error).__name__)
                        row["seconds"] = round(time.monotonic() - start, 3)
                        if row["error"] is None:
                            cache[key] = {k: row[k] for k in ("answer", "error", "seconds")}
                    row["metrics"] = score(q, selected, row.get("answer"))
                    rows.append(row)
                    save(directory / "answers.json", rows)
                    if (
                        calls.limits.consecutive_timeouts >= 2
                        or errors >= 5
                        or time.monotonic() - calls.limits.started >= 1800
                    ):
                        raise RuntimeError("Predeclared runtime stop; no further attempts")
    except Exception as error:
        failure = type(error).__name__ + ": " + str(error)
    finally:
        if calls:
            await calls.close()
        save(directory / "answers.json", rows)
        save(
            directory / "stage.json",
            {
                "evaluation_complete": len(rows) == 36 and not errors and not failure,
                "failure": failure,
                "runtime_failures": errors,
                "logical_answers": len(rows),
                "http_requests": len(calls.records) if calls else 0,
                "seconds": round(time.monotonic() - tick, 3),
                "request_seconds": round(sum(r["seconds"] for r in calls.records), 3)
                if calls
                else 0,
                "usage": {
                    k: sum(r.get("usage", {}).get(k, 0) for r in calls.records)
                    for k in ("prompt_tokens", "completion_tokens")
                }
                if calls
                else None,
                "failed_usage_unknown": sum("usage" not in r for r in calls.records)
                if calls
                else 0,
                "native": {
                    "pid": guard.process.pid,
                    "returncode": guard.process.returncode,
                    "peak_rss_bytes": guard.peak_rss_bytes,
                    "cap": guard.cap,
                    "exceeded": guard.exceeded,
                    "monitor_failed": guard.monitor_failed,
                }
                if guard
                else None,
            },
        )
    print(json.dumps(read(directory / "stage.json")), flush=True)


def audit(size):
    frozen()
    directory = HOME / size
    rows, decisions = read(directory / "answers.json"), read(directory / "audit.json")
    if len(decisions) != len(rows):
        raise ValueError("Every completed or failed record needs a hash-bound audit")
    by_key = {(d["id"], d["arm"]): d for d in decisions}
    questions = read(HOME / "questions.json")
    by_id = {q["id"]: q for q in questions}
    for row in rows:
        row["metrics"] = audited_score(by_id[row["id"]], row, by_key[row["id"], row["arm"]])
    save(directory / "answers-audited.json", rows)
    summary = {}
    for arm in ARMS:
        expected = [q for q in questions if q["expected"]]
        records = [r for r in rows if r["arm"] == arm]
        summary[arm] = {
            "planned": 12,
            "completed": sum(r.get("answer") is not None and not r.get("error") for r in records),
            "answers_correct": sum(r["metrics"]["answer_correct"] for r in records),
            "answerable": len(expected),
            "citations_complete": sum(bool(r["metrics"]["citation_complete"]) for r in records),
            "qualified": False,
        }
    save(directory / "summary.json", summary)
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("prepare", "run", "audit"))
    parser.add_argument("--model", choices=("4b", "7b"))
    args = parser.parse_args()
    if args.stage == "prepare":
        prepare()
    elif args.stage == "run":
        asyncio.run(run(args.model))
    else:
        audit(args.model)
