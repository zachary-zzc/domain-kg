"""One new, frozen 40-question 4B run. No score-driven retries or KG mutations."""

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field

import httpx
import numpy as np

from eval import hybrid_run as native
from eval.holdout_pilot import (
    audited_score,
    check_files,
    fingerprint,
    qualifies,
    score,
    select,
    sha256,
    summaries,
)
from eval.hybrid_paths import name_graph
from eval.hybrid_pilot import load_corpus
from eval.recursive_run import load_sources
from eval.recursive_runtime import CallLimits
from scripts.native_model import CACHE, ROOT

WORK = ROOT / ".cache/holdout-4b"
CASES = ROOT / "eval/holdout_cases.json"
CODE = (
    "eval/holdout_cases.json",
    "eval/holdout_pilot.py",
    "eval/holdout_run.py",
    "eval/hybrid_paths.py",
    "eval/hybrid_pilot.py",
    "eval/hybrid_run.py",
    "eval/hybrid_audit.py",
    "eval/recursive_runtime.py",
    "eval/recursive_run.py",
    "scripts/native_model.py",
    "src/domain_kg/providers/native_process.py",
    "config/medical-model.approved.json",
    "config/model-runtime.proposed.json",
    "config/laptop-smoke.json",
    "docs/superpowers/plans/2026-10-19-4b-unseen-holdout.md",
)


@dataclass
class HoldoutLimits:
    endpoint: str
    calls: int = 0
    output_tokens: int = 0
    consecutive_timeouts: int = 0
    started: float = field(default_factory=time.monotonic)

    def __post_init__(self):
        CallLimits(self.endpoint)  # Reuse the established exact loopback/port validation.

    def reserve(self):
        if self.consecutive_timeouts >= 2:
            raise RuntimeError("Two consecutive local timeouts; holdout halted")
        if (
            self.calls >= 80
            or time.monotonic() - self.started >= 3600
            or self.output_tokens >= 10240
        ):
            raise RuntimeError("New holdout model budget exhausted")
        self.calls += 1

    def outcome(self, *, timed_out):
        self.consecutive_timeouts = self.consecutive_timeouts + 1 if timed_out else 0


def configure():
    native.WORK = WORK


def frozen():
    manifest = native.read("manifest.json")
    check_files(ROOT, manifest["files"])
    check_files(WORK, manifest["artifacts"])
    _, hashes = load_sources()
    if hashes != manifest["source_hashes"]:
        raise RuntimeError("Frozen source changed")
    return manifest, native.read("corpus.json"), json.loads(CASES.read_bytes())


async def prepare():
    if WORK.exists():
        raise RuntimeError("Holdout already exists; never overwrite or silently rerun")
    parent, hashes = load_sources()
    config = json.loads(CASES.read_bytes())
    old = json.loads((ROOT / "eval/hybrid_cases.json").read_bytes())
    previous_blocks = {i for row in old["chunks"] for i in row.get("blocks", [])}
    if (
        len(config["queries"]) != 40
        or sum(bool(q["expected"]) for q in config["queries"]) != 36
        or any(previous_blocks.intersection(c.get("blocks", [])) for c in config["chunks"])
        or {q["query"] for q in old["queries"]} & {q["query"] for q in config["queries"]}
    ):
        raise ValueError("Invalid new-question/source separation")
    corpus = load_corpus(parent, config)
    ids = {c["id"] for c in corpus}
    if len({q["id"] for q in config["queries"]}) != 40 or any(
        not set(q["expected"]) <= ids for q in config["queries"]
    ):
        raise ValueError("Invalid question/reference IDs")
    WORK.mkdir(mode=0o700)
    native.save("corpus.json", corpus)
    native.save("graph.json", name_graph(corpus))
    settings = json.loads((ROOT / "config/medical-model.approved.json").read_bytes())
    settings.update(embedding_window_tokens=512, batch_tokens=16, microbatch_tokens=16)
    native.save("native-manifest.json", settings)
    manifest = {
        "status": "frozen_preparing",
        "protocol": "unseen-dense-vs-source-graph-v1",
        "source_hashes": hashes,
        "files": {p: sha256(ROOT / p) for p in CODE},
        "artifacts": {
            p: sha256(WORK / p) for p in ("corpus.json", "graph.json", "native-manifest.json")
        },
        "annotation": config["annotation"],
        "limits": {
            "top_k": 3,
            "context_chars": 1800,
            "path_steps": 2,
            "chat_attempts": 80,
            "chat_seconds": 3600,
            "completion_tokens": 10240,
            "max_failed_strategies": 5,
            "max_runtime_errors": 5,
        },
        "gate": {
            "answer_accuracy": 0.85,
            "citation_completeness": 0.90,
            "no_worse_than_vector": True,
            "semantic_audit_required": True,
        },
        "answer_prompt_sha256": fingerprint(
            native.answer_request("MODEL", "QUERY", [{"id": "SOURCE", "text": "TEXT"}])
        ),
    }
    native.save("manifest.json", manifest)
    native.save(
        "budget.json", {"calls": 0, "seconds": 0, "output_tokens": 0, "consecutive_timeouts": 0}
    )
    embedding_settings = json.loads((ROOT / "config/model-runtime.proposed.json").read_bytes())[
        "embeddings"
    ]
    rows = [("chunk/" + c["id"], c["text"]) for c in corpus]
    rows += [("query/" + q["id"], q["query"]) for q in config["queries"]]
    vectors, records = {}, []
    async with native.tracked_model("embedding", "embedding") as guard:
        async with httpx.AsyncClient(
            trust_env=False, follow_redirects=False, timeout=120
        ) as client:
            headers = {"Authorization": "Bearer " + (CACHE / "llm-api-key").read_text().strip()}
            for key, text in rows:
                started = time.monotonic()
                pending, pieces, pooled = [text], [], []
                while pending:
                    await guard.require_alive()
                    part = pending.pop(0)
                    tokenized = await client.post(
                        embedding_settings["endpoint"].removesuffix("/v1") + "/tokenize",
                        headers=headers,
                        json={"content": part, "add_special": True},
                    )
                    tokenized.raise_for_status()
                    count = len(tokenized.json()["tokens"])
                    if count > 24:
                        pending[0:0] = native.split_window(part)
                        continue
                    response = await client.post(
                        embedding_settings["endpoint"] + "/embeddings",
                        headers=headers,
                        json={
                            "model": embedding_settings["served_model_name"],
                            "input": [part],
                            "encoding_format": "float",
                        },
                    )
                    response.raise_for_status()
                    value = response.json()
                    vector = np.asarray(value["data"][0]["embedding"], dtype=float)
                    if (
                        vector.shape != (1024,)
                        or not np.isfinite(vector).all()
                        or not np.linalg.norm(vector)
                    ):
                        raise RuntimeError("Invalid embedding")
                    pooled.append(vector / np.linalg.norm(vector) * len(part.encode()))
                    pieces.append({"text": part, "tokens": count, "usage": value.get("usage")})
                if "".join(p["text"] for p in pieces) != text:
                    raise RuntimeError("Incomplete source coverage")
                vector = np.sum(pooled, axis=0)
                if not np.linalg.norm(vector):
                    raise RuntimeError("Zero pooled embedding")
                vectors[key] = (vector / np.linalg.norm(vector)).tolist()
                records.append(
                    {"key": key, "pieces": pieces, "seconds": round(time.monotonic() - started, 3)}
                )
                native.save("embeddings.json", vectors)
                native.save("embedding-calls.json", records)
                print(json.dumps({"embedded": key}), flush=True)
    manifest["artifacts"]["embeddings.json"] = sha256(WORK / "embeddings.json")
    manifest["status"] = "prepared_frozen"
    native.save("manifest.json", manifest)


async def evaluate():
    manifest, corpus, config = frozen()
    if manifest["status"] != "prepared_frozen" or (WORK / "answers.json").exists():
        raise RuntimeError("Not a fresh prepared evaluation")
    vectors, graph = native.read("embeddings.json"), native.read("graph.json")
    matrix = [vectors["chunk/" + c["id"]] for c in corpus]
    texts = {c["id"]: c["text"] for c in corpus}
    records = []
    for question in config["queries"]:
        for arm in ("V", "D"):
            selected = select(corpus, matrix, vectors["query/" + question["id"]], arm, graph)
            records.append(
                {
                    "id": question["id"],
                    "split": question["split"],
                    "arm": arm,
                    "selected": selected,
                    "context_chars": sum(len(texts[i]) for i in selected),
                    "answer": None,
                    "error": "not_run",
                    "metrics": score(question, selected, None),
                }
            )
    native.save("retrievals.json", records)
    native.save("answers.json", records)
    manifest["artifacts"]["retrievals.json"] = sha256(WORK / "retrievals.json")
    manifest["status"] = "answering_frozen"
    native.save("manifest.json", manifest)
    cache, errors = {}, 0
    async with native.tracked_model("chat", "answer") as guard:
        calls = native.calls_for(guard)
        calls.limits = HoldoutLimits(calls.connection.endpoint)
        try:
            for index, question in enumerate(config["queries"]):
                for arm in ("V", "D") if index % 2 == 0 else ("D", "V"):
                    check_files(ROOT, manifest["files"])
                    row = next(r for r in records if r["id"] == question["id"] and r["arm"] == arm)
                    request = native.answer_request(
                        calls.profile.extraction_model,
                        question["query"],
                        [{"id": i, "text": texts[i]} for i in row["selected"]],
                    )
                    key = fingerprint(request)
                    row["request_sha256"] = key
                    if key in cache:
                        outcome = cache[key]
                        row["cached_identical_request"] = True
                    else:
                        calls.case_id = question["id"] + "/" + arm
                        started = time.monotonic()
                        try:
                            answer = await calls.invoke("unseen_evidence_answer", request)
                            outcome = {"answer": answer, "error": None}
                        except Exception as error:
                            outcome = {"answer": None, "error": type(error).__name__}
                            errors += 1
                        outcome["seconds"] = round(time.monotonic() - started, 3)
                        cache[key] = outcome
                        row["cached_identical_request"] = False
                    row.update(outcome)
                    row["metrics"] = score(question, row["selected"], row["answer"])
                    native.save("answers.json", records)
                    native.save("summary-labels.json", summaries(records, config["queries"]))
                    if calls.limits.consecutive_timeouts >= 2 or errors >= 5:
                        raise RuntimeError("Holdout runtime failure stop; no automatic retry")
        finally:
            await calls.close()
            native.save_budget(calls)
            native.save("answers.json", records)
            native.save("summary-labels.json", summaries(records, config["queries"]))
    manifest["status"] = "completed_pending_semantic_audit"
    native.save("manifest.json", manifest)


def audit():
    manifest, _, config = frozen()
    questions = {q["id"]: q for q in config["queries"]}
    records, decisions = native.read("answers.json"), native.read("audit.json")
    expected_keys = {q["id"] + "/" + arm for q in config["queries"] for arm in ("V", "D")}
    if set(decisions) != expected_keys:
        raise ValueError("Incomplete semantic audit")
    audited = [
        {
            **row,
            "metrics": audited_score(
                questions[row["id"]], row, decisions[row["id"] + "/" + row["arm"]]
            ),
        }
        for row in records
    ]
    stats = summaries(audited, config["queries"])
    native.save("answers-audited.json", audited)
    native.save("summary.json", stats)
    native.save(
        "decision.json",
        {
            "qualified": qualifies(stats),
            "thresholds": manifest["gate"],
            "answers_sha256": sha256(WORK / "answers.json"),
            "audit_sha256": sha256(WORK / "audit.json"),
        },
    )
    manifest["status"] = "qualified" if qualifies(stats) else "completed_not_qualified"
    native.save("manifest.json", manifest)
    print(
        json.dumps({"status": manifest["status"], "V": stats["V/all"], "D": stats["D/all"]}),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("prepare", "evaluate", "audit"))
    arguments = parser.parse_args()
    configure()
    if arguments.stage == "audit":
        audit()
    else:
        asyncio.run(prepare() if arguments.stage == "prepare" else evaluate())
