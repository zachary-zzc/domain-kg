"""Transport-only recovery: frozen algorithms/payloads, bounded API retries.

429/timeouts/5xx are retried at most twice; semantic/protocol/content-filter
failures are never retried. All HTTP attempts remain immutable checkpoints.
"""

import argparse
import asyncio
import json
import re
import time
from pathlib import Path

import httpx
import numpy as np

from eval import dual_path_data as data
from eval.dual_path_run import (
    EMBED_MODEL,
    RunBudget,
    Runner,
    answer_jobs,
    dense_ranks,
    graph_facts,
    graph_jobs,
    verify,
)
from eval.public_qa_data import MODELS, fingerprint
from eval.public_qa_prepare import file_hash, save

RETRY = {"http_429", "timeout", "http_500", "http_502", "http_503", "http_504"}


class TransportBudget(RunBudget):
    def add(self, model, usage):
        unknown_before = self.unknown
        super().add(model, usage)
        if model == EMBED_MODEL and self.unknown > unknown_before:
            # Documented v4 maximum: 10 inputs * 8192 tokens * RMB .5 / 1M
            # = .04096. Reserve .05 even for rejected/unknown calls, never zero.
            self.reserved -= 0.95


def logical_rows(rows):
    groups = {}
    for row in rows:
        label = re.sub(r"#transport-[12]$", "", row["label"])
        key = row["kind"], row["model"], label
        groups.setdefault(key, []).append(row)
    selected = []
    for (_, _, label), attempts in groups.items():
        attempts.sort(key=lambda r: ("#transport-" in r["label"], r["label"]))
        if len({r["request_sha256"] for r in attempts}) != 1:
            raise ValueError("retry changed request")
        if len(attempts) > 3 or len({r["label"] for r in attempts}) != len(attempts):
            raise ValueError("duplicate or excessive attempts")
        success = [r for r in attempts if r["error"] is None]
        if len(success) > 1:
            raise ValueError("successful inference repeated")
        chosen = success[0] if success else attempts[-1]
        selected.append(
            {**chosen, "label": label, "attempt_identities": [r["identity"] for r in attempts]}
        )
    return selected


class RecoveryRunner(Runner):
    def __init__(self, root, key):
        super().__init__(root, key)
        self.budget = TransportBudget()
        for row in self.rows + json.loads((root / "prior-usage.json").read_text()):
            self.budget.add(row["model"], row.get("usage"))
        self.locks, self.next_start = {}, {}

    async def throttle(self, model):
        lock = self.locks.setdefault(model, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            start = max(now, self.next_start.get(model, now))
            self.next_start[model] = start + 0.5
            await asyncio.sleep(max(0, start - now))

    async def call(self, client, kind, model, label, payload, docs=None, case=None):
        for attempt in range(3):
            actual_label = label if attempt == 0 else label + f"#transport-{attempt}"
            identity = fingerprint([kind, model, actual_label])
            previous = self.existing.get(identity)
            if previous is not None:
                if previous["request_sha256"] != fingerprint(payload):
                    raise ValueError("retry changed request")
                row = previous
            else:
                if self.budget.stopped():
                    return None
                await self.throttle(model)
                row = await super().call(client, kind, model, actual_label, payload, docs, case)
            if row is None or row["error"] not in RETRY:
                return row
            if attempt < 2 and previous is None:
                await asyncio.sleep(10 * (attempt + 1))
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

        await asyncio.gather(*(worker() for _ in range(16)))

    async def embed(self, client, label, texts):
        # Sequential scheduling gives at most 2 embedding requests/sec. Existing
        # successes are read from disk, not called again.
        blocks = []
        for i in range(0, len(texts), 10):
            payload = {
                "model": EMBED_MODEL,
                "input": texts[i : i + 10],
                "dimensions": 1024,
                "encoding_format": "float",
            }
            row = await self.call(client, "embedding", EMBED_MODEL, f"{label}:{i}", payload)
            if row is None or row["error"]:
                raise ValueError("embedding recovery incomplete")
            saved = json.loads((self.root / "calls" / (row["identity"] + ".json")).read_text())
            blocks.append(np.asarray(saved["vectors"], dtype="float32"))
        array = np.concatenate(blocks)
        return array / np.linalg.norm(array, axis=1, keepdims=True)


async def recover(root, secret):
    verify(root, "formal-frozen.json")
    settings = root / "transport-settings.json"
    config = {
        "code_sha256": file_hash(Path(__file__)),
        "original_frozen_sha256": file_hash(root / "formal-frozen.json"),
        "max_attempts": 3,
        "per_model_requests_per_second": 2,
        "embedding_scheduling": "sequential, maximum two requests per second",
        "retry_errors": sorted(RETRY),
        "embedding_unknown_reserve_rmb": 0.05,
        "explanation": (
            "Correct rate limiting and missing embedding-usage upper bound; "
            "no algorithm, question, prompt or successful response changed."
        ),
    }
    if settings.exists() and json.loads(settings.read_text()) != config:
        raise ValueError("transport configuration changed")
    if not settings.exists():
        save(settings, config)
    if secret.stat().st_mode & 0o077:
        raise ValueError("private key file required")
    runner = RecoveryRunner(root, secret.read_text().strip())
    docs = json.loads((root / "corpus.json").read_text())
    cases = json.loads((root / "cases.json").read_text())
    qual = json.loads((root / "qualification.json").read_text())
    models = [m for m in MODELS if qual[m]["graph_eligible"] and qual[m]["reader_eligible"]]
    async with httpx.AsyncClient(timeout=None, limits=httpx.Limits(max_connections=16)) as client:
        try:
            vectors = await runner.embed(
                client, "formal-docs", [d["title"] + "\n" + d["text"] for d in docs]
            )
            queries = await runner.embed(client, "formal-queries", [c["question"] for c in cases])
            ranks = dense_ranks(vectors, queries, docs)
            save(root / "vector-ranks.json", ranks)
            await runner.pool(client, graph_jobs("formal_graph", docs, models))
            selected = logical_rows(runner.rows)
            if sum(r["kind"] == "formal_graph" for r in selected) != len(data.batches(docs)) * len(
                models
            ):
                save(
                    root / "stop.json",
                    {"reason": "construction_incomplete", "budget": vars(runner.budget)},
                )
                return
            graphs = {m: graph_facts(selected, "formal_graph", m) for m in models}
            records, jobs = answer_jobs(cases, docs, ranks, graphs, models, "formal_answer")
            path = root / "formal-retrieval.json"
            if path.exists() and json.loads(path.read_text()) != records:
                raise ValueError("retrieval changed on resume")
            if not path.exists():
                save(path, records)
            await runner.pool(client, jobs)
            selected = logical_rows(runner.rows)
            save(
                root / "selection.json",
                {
                    r["kind"] + "|" + r["model"] + "|" + r["label"]: r["attempt_identities"]
                    for r in selected
                },
            )
        finally:
            save(
                root / "transport-exit.json",
                {
                    "budget": vars(runner.budget),
                    "time": time.time(),
                    "logical_calls": len(logical_rows(runner.rows)),
                },
            )
            print(
                json.dumps(
                    {"budget": vars(runner.budget), "logical_calls": len(logical_rows(runner.rows))}
                ),
                flush=True,
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--secret-file", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(recover(args.root, args.secret_file))
