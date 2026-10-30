"""Bounded cloud-only, checkpointed dual-path construction/retrieval evaluation."""

import argparse
import asyncio
import json
import os
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np

from eval import dual_path_data as data
from eval import dual_path_retrieval as retrieval
from eval.public_qa_data import MODELS, fingerprint
from eval.public_qa_metrics import parse_completion, score_case
from eval.public_qa_prepare import file_hash, save
from eval.public_qa_run import ENDPOINT, PRICES, bounded_call

ROOT = Path(".cache/dual-path-2026-10-20")
OLD = Path(".cache/public-qa-2026-10-19")
EMBED_MODEL = "text-embedding-v4"
EMBED_ENDPOINT = ENDPOINT.replace("/chat/completions", "/embeddings")
ARMS = ("V6", "V12", "G6", "U12")
CONCURRENCY = 16


@dataclass
class RunBudget:
    calls: int = 0
    cost: float = 0
    reserved: float = 0
    input_tokens: int = 0
    output_tokens: int = 0
    unknown: int = 0

    def add(self, model, usage):
        self.calls += 1
        if model == EMBED_MODEL:
            inp = usage.get("total_tokens") if isinstance(usage, dict) else None
            out = 0
            prices = (0.5, 0)
        else:
            inp = usage.get("prompt_tokens") if isinstance(usage, dict) else None
            out = usage.get("completion_tokens") if isinstance(usage, dict) else None
            prices = PRICES[model]
        if any(type(v) is not int or v < 0 for v in (inp, out)):
            self.unknown += 1
            if any(prices):
                self.reserved += 1
            return
        self.input_tokens += inp
        self.output_tokens += out
        self.cost += (inp * prices[0] + out * prices[1]) / 1_000_000

    def stopped(self):
        return (
            self.cost + self.reserved >= 98
            or self.calls >= 30000
            or self.output_tokens >= 12_000_000
        )


def load_rows(directory):
    rows, seen = [], set()
    for path in sorted(directory.glob("*.json")):
        row = json.loads(path.read_text())
        row.pop("vectors", None)
        if row["identity"] in seen:
            raise ValueError("duplicate result identity")
        seen.add(row["identity"])
        rows.append(row)
    return rows


def parse_answer(content, case):
    obj = data.json_object(content)
    wire = case.get("wire_ids")
    allowed_case = case
    if wire:
        allowed_case = {**case, "contexts": [{"id": ident} for ident in wire]}
    answer = parse_completion(json.dumps(obj), allowed_case) if obj is not None else None
    if answer is not None and wire:
        answer["citations"] = [wire[c] for c in answer["citations"]]
    return answer, isinstance(content, str) and content.strip().startswith("{")


def response_content(result):
    response = result.get("response")
    usage = response.get("usage") if isinstance(response, dict) else None
    if result["error"]:
        return None, result["error"], usage
    try:
        choice = response["choices"][0]
        if choice["finish_reason"] != "stop":
            return None, "finish_" + str(choice["finish_reason"]), usage
        message = choice["message"]
        if message.get("tool_calls") or message.get("refusal"):
            return None, "unexpected_tool_or_refusal", usage
        return message["content"], None, usage
    except (KeyError, IndexError, TypeError):
        return None, "invalid_response_shape", usage


def prepare(root):
    if root.exists():
        raise ValueError("refuse to overwrite existing experiment")
    root.mkdir(mode=0o700, parents=True)
    (root / "calls").mkdir(mode=0o700)
    cases = json.loads((OLD / "formal.json").read_text())
    docs, cases = data.corpus(cases)
    fdocs, fcases, gold = data.fictional()
    public_pilot, _ = data.corpus(json.loads((OLD / "pilot.json").read_text()))
    # Excluded public pilot passages exercise natural-text protocol only.
    pilot_docs = fdocs + public_pilot[:8]
    save(root / "corpus.json", docs)
    save(root / "cases.json", cases)
    save(root / "pilot-docs.json", pilot_docs)
    save(root / "fiction-docs.json", fdocs)
    save(root / "fiction-cases.json", fcases)
    save(root / "fiction-gold.json", gold)
    manifest = {
        "created": "2026-10-20",
        "raw_library_bytes": 219604885,
        "questions": len(cases),
        "paragraphs": len(docs),
        "text_utf8_bytes": sum(len(d["text"].encode()) for d in docs),
        "dataset_counts": dict(Counter(c["dataset"] for c in cases)),
        "models": MODELS,
        "embedding_model": EMBED_MODEL,
        "embedding_dimensions": 1024,
        "construction_batches_per_model": len(data.batches(docs)),
        "character_budget": 12000,
        "max_calls_in_flight": 5,
        "deadline_seconds": 60,
        "hard_list_price_budget_rmb": 100,
        "arms": ARMS,
        "old_formal_sha256": file_hash(OLD / "formal.json"),
        "source_attribution": str(OLD / "data-manifest.json"),
        "warning": (
            "Global corpus changes original distractor/unanswerable context boundaries; "
            "no-answer scores are separate."
        ),
    }
    save(root / "manifest.json", manifest)
    freeze(root, "pilot-frozen.json")
    print(json.dumps(manifest), flush=True)


def freeze(root, name):
    names = [
        "corpus.json",
        "cases.json",
        "pilot-docs.json",
        "fiction-docs.json",
        "fiction-cases.json",
        "fiction-gold.json",
        "manifest.json",
    ]
    if name == "formal-frozen.json":
        names += ["qualification.json", "estimate.json", "formal-settings.json", "prior-usage.json"]
    files = [root / n for n in names]
    files += list(Path("eval").glob("dual_path_*.py"))
    files += [Path("docs/superpowers/plans/2026-10-20-dual-path-model-size.md")]
    save(root / name, {str(p): file_hash(p) for p in files})


def verify(root, name):
    for path, digest in json.loads((root / name).read_text()).items():
        if file_hash(Path(path)) != digest:
            raise ValueError("frozen input changed: " + path)


class Runner:
    def __init__(self, root, key):
        self.root, self.key, self.budget = root, key, RunBudget()
        self.rows = load_rows(root / "calls")
        self.existing = {r["identity"]: r for r in self.rows}
        for r in self.rows:
            self.budget.add(r["model"], r.get("usage"))
        prior = root / "prior-usage.json"
        if prior.exists():
            for r in json.loads(prior.read_text()):
                self.budget.add(r["model"], r.get("usage"))

    async def call(self, client, kind, model, label, payload, docs=None, case=None):
        identity = fingerprint([kind, model, label])
        request_hash = fingerprint(payload)
        if identity in self.existing:
            previous = self.existing[identity]
            if previous["request_sha256"] != request_hash:
                raise ValueError("resume request changed")
            return previous
        if self.budget.stopped():
            return None
        endpoint = EMBED_ENDPOINT if kind == "embedding" else ENDPOINT
        result = await bounded_call(client, endpoint, payload, self.key, deadline=60)
        response = result.get("response")
        usage = response.get("usage") if isinstance(response, dict) else None
        row = {
            "identity": identity,
            "kind": kind,
            "model": model,
            "label": label,
            "request_sha256": request_hash,
            "seconds": result["seconds"],
            "http_status": result["http_status"],
            "error": result["error"],
            "usage": usage,
            "raw": response,
        }
        if kind == "embedding":
            if row["error"] is None:
                try:
                    entries = response["data"]
                    vectors = [v["embedding"] for v in sorted(entries, key=lambda v: v["index"])]
                    valid = sorted(v["index"] for v in entries) == list(
                        range(len(payload["input"]))
                    )
                    array = np.asarray(vectors, dtype="float32")
                    if (
                        not valid
                        or array.shape != (len(payload["input"]), 1024)
                        or not np.isfinite(array).all()
                        or (np.linalg.norm(array, axis=1) == 0).any()
                    ):
                        raise ValueError("invalid embeddings")
                    row["vectors"] = vectors
                    # Retain vectors once, avoid duplicate raw array storage.
                    row["raw"] = {k: v for k, v in response.items() if k != "data"}
                except (KeyError, ValueError, TypeError):
                    row["error"] = "invalid_embeddings"
        else:
            content, error, _ = response_content(result)
            row["error"] = error
            if error is None:
                if kind.endswith("graph"):
                    parsed = data.parse_graph(content, docs)
                    row["graph"] = parsed
                    if parsed is None:
                        row["error"] = "invalid_graph_protocol"
                else:
                    answer, raw_json = parse_answer(content, case)
                    row["answer"], row["raw_json"] = answer, raw_json
                    row["scores"] = score_case(case, answer)
                    if answer is None:
                        row["error"] = "invalid_answer_protocol"
        self.budget.add(model, usage)
        path = self.root / "calls" / (identity + ".json")
        if path.exists():
            raise ValueError("checkpoint collision")
        save(path, row)
        if kind == "embedding":
            row.pop("vectors", None)
        self.existing[identity] = row
        self.rows.append(row)
        if len(self.rows) % 25 == 0:
            print(
                json.dumps(
                    {"completed": len(self.rows), "last_kind": kind, "budget": vars(self.budget)}
                ),
                flush=True,
            )
            save(
                self.root / "progress.json",
                {"updated_at": time.time(), "budget": vars(self.budget), "last_kind": kind},
            )
        return row

    async def pool(self, client, jobs):
        iterator = iter(jobs)

        async def worker():
            for args in iterator:
                if self.budget.stopped():
                    return
                if "payload" not in args:
                    args = {
                        **args,
                        "payload": data.answer_request(
                            args["model"], args["case"], args["case"]["contexts"]
                        ),
                    }
                await self.call(client, **args)

        await asyncio.gather(*(worker() for _ in range(CONCURRENCY)))

    async def embed(self, client, label, texts):
        jobs = []
        for i in range(0, len(texts), 10):
            jobs.append(
                {
                    "kind": "embedding",
                    "model": EMBED_MODEL,
                    "label": f"{label}:{i}",
                    "payload": {
                        "model": EMBED_MODEL,
                        "input": texts[i : i + 10],
                        "dimensions": 1024,
                        "encoding_format": "float",
                    },
                }
            )
        await self.pool(client, jobs)
        blocks = []
        for job in jobs:
            ident = fingerprint([job["kind"], job["model"], job["label"]])
            row = self.existing.get(ident)
            if row is None or row["error"]:
                raise ValueError("embedding incomplete; no silent lexical fallback")
            saved = json.loads((self.root / "calls" / (ident + ".json")).read_text())
            blocks.append(np.asarray(saved["vectors"], dtype="float32"))
        array = np.concatenate(blocks)
        return array / np.linalg.norm(array, axis=1, keepdims=True)


def graph_facts(rows, kind, model):
    return [
        f
        for r in rows
        if r["kind"] == kind and r["model"] == model and r.get("graph")
        for f in r["graph"]["facts"]
    ]


def graph_jobs(kind, docs, models, pilot=False):
    grouped = data.batches(docs, max_docs=4 if pilot else 8)
    # Interleave sizes so a bounded stop does not selectively finish one model.
    return [
        {
            "kind": kind,
            "model": model,
            "label": str(i),
            "payload": data.extraction_request(model, batch),
            "docs": batch,
        }
        for i, batch in enumerate(grouped)
        for model in models
    ]


def answer_jobs(cases, docs, ranks, graphs, models, kind, arm_names=ARMS):
    lookup = {d["id"]: d for d in docs}
    if any(f["d"] not in lookup for m in models for f in graphs[m]):
        raise ValueError("graph source outside corpus")
    indexes = {m: retrieval.GraphIndex(graphs[m]) for m in models}
    records, jobs = [], []
    for c, vector_rank in zip(cases, ranks, strict=True):
        for model in models:
            graph_rank = indexes[model].recall(c["question"])
            arms = retrieval.contexts(vector_rank, graph_rank, lookup)
            if not set(arms["V6"]) <= set(arms["U12"]):
                raise ValueError("fusion evicted vector source")
            for arm in arm_names:
                sources = retrieval.source_units(arms[arm], lookup)
                question = {
                    **c,
                    "contexts": sources,
                    "wire_ids": {f"e{i}": s["id"] for i, s in enumerate(sources)},
                }
                label = c["id"] + "|" + arm
                records.append(
                    {
                        "id": c["id"],
                        "model": model,
                        "arm": arm,
                        "documents": arms[arm],
                        "retrieval": retrieval.support_scores(c, arms[arm], lookup),
                    }
                )
                jobs.append(
                    {
                        "kind": kind,
                        "model": model,
                        "label": label,
                        "case": question,
                    }
                )
    return records, jobs


def dense_ranks(vectors, query_vectors, docs):
    scores = query_vectors @ vectors.T
    return [[docs[int(i)]["id"] for i in np.argsort(-row, kind="stable")[:12]] for row in scores]


async def pilot(runner, client):
    root = runner.root
    verify(root, "pilot-frozen.json")
    # Actual embedding call checks this key's capability, not just chat auth.
    await runner.embed(client, "smoke", ["Fictional project uses a device."])
    docs = json.loads((root / "pilot-docs.json").read_text())
    await runner.pool(client, graph_jobs("pilot_graph", docs, MODELS, pilot=True))
    fdocs = json.loads((root / "fiction-docs.json").read_text())
    cases = json.loads((root / "fiction-cases.json").read_text())
    gold = json.loads((root / "fiction-gold.json").read_text())
    vectors = await runner.embed(
        client, "fiction-docs", [d["title"] + "\n" + d["text"] for d in fdocs]
    )
    qvectors = await runner.embed(client, "fiction-queries", [c["question"] for c in cases])
    ranks = dense_ranks(vectors, qvectors, fdocs)
    fictional_ids = {d["id"] for d in fdocs}
    graphs = {
        m: [f for f in graph_facts(runner.rows, "pilot_graph", m) if f["d"] in fictional_ids]
        for m in MODELS
    }
    qualification = {}
    for model in MODELS:
        rows = [r for r in runner.rows if r["kind"] == "pilot_graph" and r["model"] == model]
        facts = [f for f in graphs[model] if f["d"] in {d["id"] for d in fdocs}]
        qualification[model] = {
            "construction": data.fact_scores(gold, facts),
            "calls": len(rows),
            "usable_calls": sum(not r["error"] for r in rows),
            "errors": dict(Counter(r["error"] for r in rows if r["error"])),
            "graph_eligible": bool(facts) and sum(not r["error"] for r in rows) >= len(rows) / 2,
        }
    # Every size gets actual reading calls, independently of its graph gate.
    probe_cases = [cases[0], cases[4], cases[8]]
    probe_ranks = [ranks[0], ranks[4], ranks[8]]
    _, probes = answer_jobs(probe_cases, fdocs, probe_ranks, graphs, MODELS, "pilot_probe", ("V6",))
    await runner.pool(client, probes)
    eligible = []
    for model in MODELS:
        rows = [r for r in runner.rows if r["kind"] == "pilot_probe" and r["model"] == model]
        qualification[model]["reader_usable"] = sum(not r["error"] for r in rows)
        qualification[model]["reader_errors"] = dict(
            Counter(r["error"] for r in rows if r["error"])
        )
        qualification[model]["reader_eligible"] = sum(not r["error"] for r in rows) >= 2
        if qualification[model]["graph_eligible"] and qualification[model]["reader_eligible"]:
            eligible.append(model)
    records, jobs = answer_jobs(cases, fdocs, ranks, graphs, eligible, "pilot_answer")
    save(root / "pilot-retrieval.json", records)
    await runner.pool(client, jobs)
    save(root / "qualification.json", qualification)
    # Measured average extraction usage scaled to planned full corpus.
    estimates = {}
    n_batches = len(data.batches(json.loads((root / "corpus.json").read_text())))
    for m in eligible:
        rows = [
            r
            for r in runner.rows
            if r["kind"] == "pilot_graph" and r["model"] == m and isinstance(r.get("usage"), dict)
        ]
        costs = [
            (
                r["usage"].get("prompt_tokens", 0) * PRICES[m][0]
                + r["usage"].get("completion_tokens", 0) * PRICES[m][1]
            )
            / 1e6
            for r in rows
        ]
        # Formal batches contain up to twice as many paragraphs as pilot batches.
        estimates[m] = sum(costs) / max(1, len(costs)) * n_batches * 2
    save(
        root / "estimate.json",
        {
            "eligible_models": eligible,
            "formal_graph_batches_per_model": n_batches,
            "extraction_cost_estimate_rmb": estimates,
            "pilot_budget": vars(runner.budget),
            "note": (
                "Extrapolation, not a bill; actual hard cap includes answering "
                "and missing-usage reserve."
            ),
        },
    )
    print(
        json.dumps(
            {
                "qualification": qualification,
                "extraction_cost_estimate_rmb": estimates,
                "budget": vars(runner.budget),
            }
        ),
        flush=True,
    )


async def formal(runner, client):
    root = runner.root
    if not (root / "formal-frozen.json").exists():
        save(
            root / "formal-settings.json",
            {
                "concurrency": CONCURRENCY,
                "deadline_seconds": 60,
                "budget_rmb": 100,
                "in_flight_margin_rmb": 2,
                "construction_max_sources": 8,
                "construction_source_characters": 4400,
                "construction_max_output_tokens": 4096,
                "reader_max_output_tokens": 2048,
                "arms": ARMS,
                "questions_per_arm_per_model": 1000,
                "algorithm_strategy": 3,
                "further_optimization": False,
            },
        )
        freeze(root, "formal-frozen.json")
    verify(root, "formal-frozen.json")
    qualification = json.loads((root / "qualification.json").read_text())
    models = [
        m
        for m in MODELS
        if qualification[m]["graph_eligible"] and qualification[m]["reader_eligible"]
    ]
    docs = json.loads((root / "corpus.json").read_text())
    cases = json.loads((root / "cases.json").read_text())
    vectors = await runner.embed(
        client, "formal-docs", [d["title"] + "\n" + d["text"] for d in docs]
    )
    queries = await runner.embed(client, "formal-queries", [c["question"] for c in cases])
    ranks = dense_ranks(vectors, queries, docs)
    save(root / "vector-ranks.json", ranks)
    await runner.pool(client, graph_jobs("formal_graph", docs, models))
    graphs = {m: graph_facts(runner.rows, "formal_graph", m) for m in models}
    records, jobs = answer_jobs(cases, docs, ranks, graphs, models, "formal_answer")
    save(root / "formal-retrieval.json", records)
    # A budget stop never launches readers against incompletely attempted graphs.
    planned_graph = len(data.batches(docs)) * len(models)
    done_graph = sum(r["kind"] == "formal_graph" for r in runner.rows)
    if done_graph != planned_graph:
        save(
            root / "stop.json",
            {
                "reason": "construction_incomplete_or_budget",
                "planned": planned_graph,
                "attempted": done_graph,
                "budget": vars(runner.budget),
            },
        )
        return
    await runner.pool(client, jobs)


def summarize(root):
    rows = load_rows(root / "calls")
    budget = RunBudget()
    for row in rows:
        budget.add(row["model"], row.get("usage"))
    prior = root / "prior-usage.json"
    if prior.exists():
        for row in json.loads(prior.read_text()):
            budget.add(row["model"], row.get("usage"))
    summaries = {}
    for prefix, case_file in (("pilot", "fiction-cases.json"), ("formal", "cases.json")):
        path = root / (prefix + "-retrieval.json")
        if not path.exists():
            continue
        records = json.loads(path.read_text())
        cases = {c["id"]: c for c in json.loads((root / case_file).read_text())}
        results = {(r["model"], r["label"]): r for r in rows if r["kind"] == prefix + "_answer"}
        groups = {}
        for m in MODELS:
            for arm in ARMS:
                members = [r for r in records if r["model"] == m and r["arm"] == arm]
                if not members:
                    continue
                matched = [results.get((m, r["id"] + "|" + arm)) for r in members]
                answerable = sum(cases[r["id"]]["answerable"] for r in members)
                n = len(members)
                group = {
                    "planned": n,
                    "attempted": sum(r is not None for r in matched),
                    "answerable": answerable,
                }
                group["errors"] = dict(
                    Counter(r["error"] for r in matched if r is not None and r["error"])
                )
                for metric in ("em", "f1", "protocol_ok"):
                    group[metric] = (
                        sum((r or {}).get("scores", {}).get(metric, 0) for r in matched) / n
                    )
                group["citation_complete"] = (
                    sum((r or {}).get("scores", {}).get("citation_complete", 0) for r in matched)
                    / answerable
                    if answerable
                    else None
                )
                for metric in ("complete", "recall"):
                    group["retrieval_" + metric] = (
                        sum(r["retrieval"][metric] or 0 for r in members) / answerable
                        if answerable
                        else None
                    )
                group["raw_json_rate"] = sum(bool((r or {}).get("raw_json")) for r in matched) / n
                group["mean_seconds"] = (
                    sum(r["seconds"] for r in matched if r is not None) / group["attempted"]
                    if group["attempted"]
                    else None
                )
                groups[m + "|" + arm] = group
        summaries[prefix] = groups
    save(
        root / "summary.json",
        {"budget": vars(budget), "groups": summaries, "unattempted_metrics_are_partial": True},
    )
    print(json.dumps({"budget": vars(budget), "groups": summaries}), flush=True)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("prepare", "pilot", "formal", "summary"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--secret-file", type=Path)
    args = parser.parse_args()
    if args.phase == "prepare":
        prepare(args.root)
        return
    if args.phase == "summary":
        summarize(args.root)
        return
    if args.secret_file is None or args.secret_file.stat().st_mode & 0o077:
        raise ValueError("private credential file required")
    key = args.secret_file.read_text().strip()
    if not key:
        raise ValueError("empty credential")
    runner = Runner(args.root, key)
    async with httpx.AsyncClient(
        timeout=None, limits=httpx.Limits(max_connections=CONCURRENCY)
    ) as client:
        try:
            await (pilot(runner, client) if args.phase == "pilot" else formal(runner, client))
        finally:
            save(
                args.root / "last-exit.json",
                {
                    "phase": args.phase,
                    "pid": os.getpid(),
                    "budget": vars(runner.budget),
                    "time": time.time(),
                },
            )
            summarize(args.root)


if __name__ == "__main__":
    asyncio.run(main())
