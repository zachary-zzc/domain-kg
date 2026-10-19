"""Bounded native development and fresh-question separate-recall/filter evaluation."""

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field

import httpx
import numpy as np

from eval import hybrid_run as native
from eval.fusion_pilot import apply_filter, candidate_pool, graph_recall
from eval.holdout_pilot import (
    audited_score,
    check_files,
    dense_rank,
    fingerprint,
    qualifies,
    score,
    sha256,
    summaries,
)
from eval.hybrid_paths import name_graph
from eval.hybrid_pilot import load_corpus
from eval.recursive_run import load_sources
from eval.recursive_runtime import CallLimits
from scripts.native_model import CACHE, ROOT

HOME = ROOT / ".cache/dual-retrieve-filter"
OLD = ROOT / ".cache/holdout-4b"
CASES = ROOT / "eval/fusion_cases.json"
CODE = (
    "eval/fusion_pilot.py",
    "eval/fusion_run.py",
    "eval/holdout_pilot.py",
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
    "docs/superpowers/plans/2026-10-19-dual-retrieve-filter.md",
)
DEV = (
    "p15",
    "two_hop1",
    "two_hop2",
    "two_hop3",
    "three_hop1",
    "three_hop2",
    "three_hop3",
    "three_hop4",
)


@dataclass
class FusionLimits:
    endpoint: str
    calls: int = 0
    output_tokens: int = 0
    consecutive_timeouts: int = 0
    started: float = field(default_factory=time.monotonic)

    def __post_init__(self):
        CallLimits(self.endpoint)

    def reserve(self):
        if self.consecutive_timeouts >= 2:
            raise RuntimeError("Two consecutive timeouts; filter experiment halted")
        if (
            self.calls >= 96
            or time.monotonic() - self.started >= 3600
            or self.output_tokens >= 12288
        ):
            raise RuntimeError("Filter experiment cumulative budget exhausted")
        self.calls += 1

    def outcome(self, *, timed_out):
        self.consecutive_timeouts = self.consecutive_timeouts + 1 if timed_out else 0


def setup(stage):
    native.WORK = HOME / stage


def previous():
    manifest = json.loads((OLD / "manifest.json").read_bytes())
    check_files(ROOT, manifest["files"])
    check_files(OLD, manifest["artifacts"])
    return (
        json.loads((OLD / "corpus.json").read_bytes()),
        json.loads((OLD / "embeddings.json").read_bytes()),
    )


def init_stage(stage, corpus, config, vectors=None):
    path = HOME / stage
    if path.exists():
        raise RuntimeError("Stage exists; never overwrite or silently rerun")
    path.mkdir(parents=True, mode=0o700)
    setup(stage)
    if not (HOME / "budget.json").exists():
        (HOME / "budget.json").write_text(
            json.dumps({"calls": 0, "seconds": 0, "output_tokens": 0, "consecutive_timeouts": 0})
        )
    settings = json.loads((OLD / "native-manifest.json").read_bytes())
    native.save("native-manifest.json", settings)
    native.save("corpus.json", corpus)
    native.save("cases.json", config)
    native.save("graph.json", name_graph(corpus))
    if vectors is not None:
        native.save("embeddings.json", vectors)
    files = {p: sha256(ROOT / p) for p in CODE}
    if stage == "heldout":
        files["eval/fusion_cases.json"] = sha256(CASES)
    _, source_hashes = load_sources()
    artifacts = {p.name: sha256(p) for p in path.glob("*.json")}
    native.save(
        "manifest.json",
        {
            "stage": stage,
            "status": "prepared" if vectors else "preparing",
            "files": files,
            "artifacts": artifacts,
            "source_hashes": source_hashes,
            "old_manifest_sha256": sha256(OLD / "manifest.json"),
        },
    )
    for name in files:
        target = path / "frozen-files" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())


def frozen(stage):
    setup(stage)
    manifest = native.read("manifest.json")
    check_files(ROOT, manifest["files"])
    check_files(native.WORK, manifest["artifacts"])
    _, sources = load_sources()
    if sources != manifest["source_hashes"]:
        raise RuntimeError("Frozen source changed")
    return manifest, native.read("corpus.json"), native.read("cases.json")


async def prepare():
    corpus_old, vectors_old = previous()
    config = json.loads(CASES.read_bytes())
    if len(config["queries"]) != 24 or sum(bool(q["expected"]) for q in config["queries"]) != 20:
        raise ValueError("Invalid fresh test counts")
    old_questions = json.loads((ROOT / "eval/holdout_cases.json").read_bytes())["queries"]
    if {q["query"] for q in old_questions} & {q["query"] for q in config["queries"]}:
        raise ValueError("Repeated question")
    parent, _ = load_sources()
    corpus = load_corpus(parent, config)
    init_stage("heldout", corpus, config)
    reuse = {fingerprint(c["text"]): vectors_old["chunk/" + c["id"]] for c in corpus_old}
    vectors, records, inputs = {}, [], []
    for row in corpus:
        key = "chunk/" + row["id"]
        if fingerprint(row["text"]) in reuse:
            vectors[key] = reuse[fingerprint(row["text"])]
            records.append(
                {
                    "key": key,
                    "reused_source": True,
                    "text_sha256": fingerprint(row["text"]),
                    "origin_embeddings_sha256": sha256(OLD / "embeddings.json"),
                }
            )
        else:
            inputs.append((key, row["text"]))
    inputs += [("query/" + q["id"], q["query"]) for q in config["queries"]]
    settings = json.loads((ROOT / "config/model-runtime.proposed.json").read_bytes())["embeddings"]
    async with native.tracked_model("embedding", "embedding") as guard:
        async with httpx.AsyncClient(
            trust_env=False, follow_redirects=False, timeout=120
        ) as client:
            headers = {"Authorization": "Bearer " + (CACHE / "llm-api-key").read_text().strip()}
            for key, text in inputs:
                pending, pieces, pooled = [text], [], []
                while pending:
                    await guard.require_alive()
                    part = pending.pop(0)
                    tok = await client.post(
                        settings["endpoint"].removesuffix("/v1") + "/tokenize",
                        headers=headers,
                        json={"content": part, "add_special": True},
                    )
                    tok.raise_for_status()
                    count = len(tok.json()["tokens"])
                    if count > 24:
                        pending[0:0] = native.split_window(part)
                        continue
                    result = await client.post(
                        settings["endpoint"] + "/embeddings",
                        headers=headers,
                        json={
                            "model": settings["served_model_name"],
                            "input": [part],
                            "encoding_format": "float",
                        },
                    )
                    result.raise_for_status()
                    value = result.json()
                    vector = np.asarray(value["data"][0]["embedding"], dtype=float)
                    if (
                        vector.shape != (1024,)
                        or not np.isfinite(vector).all()
                        or not np.linalg.norm(vector)
                    ):
                        raise RuntimeError("Invalid embedding")
                    pieces.append({"text": part, "tokens": count, "usage": value.get("usage")})
                    pooled.append(vector / np.linalg.norm(vector) * len(part.encode()))
                if "".join(p["text"] for p in pieces) != text:
                    raise RuntimeError("Incomplete source embedding")
                vector = np.sum(pooled, axis=0)
                vectors[key] = (vector / np.linalg.norm(vector)).tolist()
                records.append({"key": key, "pieces": pieces, "reused_source": False})
                native.save("embeddings.json", vectors)
                native.save("embedding-calls.json", records)
                print(json.dumps({"embedded": key}), flush=True)
    manifest = native.read("manifest.json")
    manifest["artifacts"]["embeddings.json"] = sha256(native.WORK / "embeddings.json")
    manifest["status"] = "prepared"
    native.save("manifest.json", manifest)


async def evaluate(stage):
    if stage == "development":
        corpus, vectors = previous()
        config = json.loads((ROOT / "eval/holdout_cases.json").read_bytes())
        config["queries"] = [q for q in config["queries"] if q["id"] in DEV]
        init_stage(stage, corpus, config, vectors)
    manifest, corpus, config = frozen(stage)
    if manifest["status"] != "prepared" or (native.WORK / "answers.json").exists():
        raise RuntimeError("Evaluation not fresh")
    vectors, graph = native.read("embeddings.json"), native.read("graph.json")
    matrix = [vectors["chunk/" + c["id"]] for c in corpus]
    model_name = native.read("native-manifest.json")["served_model_name"]
    texts = {c["id"]: c["text"] for c in corpus}
    pools = {}
    for q in config["queries"]:
        vector = dense_rank(corpus, matrix, vectors["query/" + q["id"]])[:6]
        graph_result = graph_recall(corpus, graph, q["query"])
        pools[q["id"]] = {
            **candidate_pool(model_name, q["query"], corpus, vector, graph_result["selected"]),
            "graph_trace": graph_result,
        }
    native.save("pools.json", pools)
    manifest["artifacts"]["pools.json"] = sha256(native.WORK / "pools.json")
    manifest["status"] = "running"
    native.save("manifest.json", manifest)
    cache = {}
    if stage == "development":
        for row in json.loads((OLD / "answers.json").read_bytes()):
            if not row["error"]:
                cache[row["request_sha256"]] = {
                    "answer": row["answer"],
                    "error": None,
                    "seconds": row["seconds"],
                    "origin": "old_40",
                }
    rows, filters, errors = [], {}, 0
    prior = json.loads((HOME / "budget.json").read_bytes())
    native.save("budget.json", prior)
    async with native.tracked_model("chat", "filter-answer") as guard:
        calls = native.calls_for(guard)
        calls.limits = FusionLimits(
            calls.connection.endpoint,
            calls=prior["calls"],
            output_tokens=prior["output_tokens"],
            consecutive_timeouts=prior["consecutive_timeouts"],
            started=time.monotonic() - prior["seconds"],
        )

        async def invoke(request, label):
            nonlocal errors
            key = fingerprint(request)
            if key not in cache:
                calls.case_id = label
                tick = time.monotonic()
                try:
                    answer = await calls.invoke(label.split("/")[-1], request)
                    cache[key] = {"answer": answer, "error": None, "origin": stage}
                except Exception as error:
                    if calls.limits.calls >= 96 or time.monotonic() - calls.limits.started >= 3600:
                        raise
                    errors += 1
                    cache[key] = {"answer": None, "error": type(error).__name__, "origin": stage}
                cache[key]["seconds"] = round(time.monotonic() - tick, 3)
            outcome = {**cache[key], "request_sha256": key}
            await guard.require_alive()
            if calls.limits.consecutive_timeouts >= 2 or errors >= 5:
                raise RuntimeError("Runtime failures reached stop condition")
            return outcome

        try:
            for index, q in enumerate(config["queries"]):
                check_files(ROOT, manifest["files"])
                pool = pools[q["id"]]
                for arm in ("V", "D") if index % 2 == 0 else ("D", "V"):
                    if arm == "D":
                        result = await invoke(pool["request"], q["id"] + "/filter")
                        selection = apply_filter(
                            result["answer"], pool["selected"], corpus, pool["baseline"]
                        )
                        filters[q["id"]] = {**result, **selection}
                        selected = selection["selected"]
                        native.save("filters.json", filters)
                    else:
                        selected = pool["baseline"]
                    req = native.answer_request(
                        model_name, q["query"], [{"id": k, "text": texts[k]} for k in selected]
                    )
                    result = await invoke(req, q["id"] + "/" + arm + "/answer")
                    rows.append(
                        {
                            "id": q["id"],
                            "split": q["split"],
                            "arm": arm,
                            "selected": selected,
                            **result,
                            "metrics": score(q, selected, result["answer"]),
                        }
                    )
                    native.save("answers.json", rows)
        finally:
            await calls.close()
            native.save_budget(calls)
            (HOME / "budget.json").write_bytes((native.WORK / "budget.json").read_bytes())
            native.save("answers.json", rows)
            native.save("filters.json", filters)
            native.save("summary-labels.json", summaries(rows, config["queries"]))
    manifest["status"] = "completed_pending_audit"
    native.save("manifest.json", manifest)


def audit(stage):
    manifest, _, config = frozen(stage)
    questions = {q["id"]: q for q in config["queries"]}
    rows, decisions = native.read("answers.json"), native.read("audit.json")
    if set(decisions) != {q["id"] + "/" + a for q in config["queries"] for a in ("V", "D")}:
        raise ValueError("Incomplete audit")
    audited = [
        {
            **row,
            "metrics": audited_score(
                questions[row["id"]], row, decisions[row["id"] + "/" + row["arm"]]
            ),
        }
        for row in rows
    ]
    stats = summaries(audited, config["queries"])
    native.save("answers-audited.json", audited)
    native.save("summary.json", stats)
    passed = stage == "heldout" and qualifies(stats)
    native.save(
        "decision.json",
        {
            "qualified": passed,
            "stage": stage,
            "answers_sha256": sha256(native.WORK / "answers.json"),
            "audit_sha256": sha256(native.WORK / "audit.json"),
        },
    )
    manifest["status"] = "qualified" if passed else "completed_not_qualified"
    native.save("manifest.json", manifest)
    print(
        json.dumps({"stage": stage, "qualified": passed, "V": stats["V/all"], "D": stats["D/all"]}),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage", choices=("development", "prepare", "evaluate", "audit-dev", "audit")
    )
    args = parser.parse_args()
    if args.stage.startswith("audit"):
        audit("development" if args.stage == "audit-dev" else "heldout")
    else:
        asyncio.run(
            prepare()
            if args.stage == "prepare"
            else evaluate("development" if args.stage == "development" else "heldout")
        )
