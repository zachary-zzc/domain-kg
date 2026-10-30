"""Immutable API attempts for the user-authorized full-scale experiment.

No aggregate spending ceiling; finite jobs, bounded payloads, bounded retries.
An ambiguous launch is never automatically repeated. No secret is persisted.
"""

import asyncio
import json
import time

from eval.public_qa_run import bounded_call
from eval.source_role_eval import digest
from eval.source_role_run import ENDPOINT, read, write_new

PRICES = {
    "qwen3.5-397b-a17b": (1.2, 7.2),
    "qwen3.5-122b-a10b": (0.8, 6.4),
    "qwen3.6-35b-a3b": (1.8, 10.8),
    "qwen3.8-27b": (3.0, 12.0),
    "deepseek-r1-distill-qwen-1.5b": None,
}
RETRY = {"http_429", "http_500", "http_502", "http_503", "http_504", "timeout"}


def cost(model, usage):
    prices = PRICES[model]
    if prices is None or not isinstance(usage, dict):
        return None
    counts = [usage.get(k) for k in ("prompt_tokens", "completion_tokens")]
    if any(type(n) is not int or not 0 <= n <= 1_000_000 for n in counts):
        return None
    return sum(n * p for n, p in zip(counts, prices, strict=True)) / 1_000_000


class Runner:
    def __init__(self, root, key, *, interval=0.6):
        self.root, self.key, self.interval = root, key, interval
        root.mkdir(parents=True, exist_ok=True)
        self.locks, self.next_start, self.finished = {}, {}, 0

    def paths(self, label):
        stem = digest(label)
        return self.root / (stem + ".launch.json"), self.root / (stem + ".result.json")

    def reserve(self, label, payload):
        if payload["model"] not in PRICES or not 1 <= payload["max_tokens"] <= 16384:
            raise ValueError("unapproved model/output allowance")
        if len(json.dumps(payload).encode()) > 120_000:
            raise ValueError("request too large")
        launch, _ = self.paths(label)
        identity = digest(payload)
        if launch.exists():
            if read(launch)["request_sha256"] != identity:
                raise ValueError("checkpoint request changed")
            return False
        write_new(
            launch,
            {
                "label": label,
                "request_sha256": identity,
                "payload": payload,
                "launched_at": time.time(),
                "aggregate_limits": None,
            },
        )
        return True

    async def throttle(self, model):
        lock = self.locks.setdefault(model, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            due = max(now, self.next_start.get(model, now))
            interval = max(self.interval, 2.0) if model.startswith("deepseek") else self.interval
            self.next_start[model] = due + interval
            await asyncio.sleep(max(0, due - now))

    async def call(self, client, label, payload):
        for attempt in range(3):
            attempt_label = label + ":" + str(attempt)
            launch, result = self.paths(attempt_label)
            if not launch.exists():
                await self.throttle(payload["model"])
            fresh = self.reserve(attempt_label, payload)
            if not fresh:
                row = (
                    read(result)
                    if result.exists()
                    else {
                        "label": attempt_label,
                        "error": "ambiguous_launch",
                        "content": None,
                        "usage": None,
                        "actual_rmb": None,
                        "model": payload["model"],
                    }
                )
            else:
                raw = await bounded_call(client, ENDPOINT, payload, self.key, deadline=180)
                response = raw.get("response") or {}
                usage = response.get("usage")
                row = {
                    "label": attempt_label,
                    "model": payload["model"],
                    "request_sha256": digest(payload),
                    "content": None,
                    "error": raw["error"],
                    "usage": usage,
                    "actual_rmb": cost(payload["model"], usage),
                    "seconds": raw["seconds"],
                    "response": response,
                }
                if not row["error"]:
                    try:
                        choice = response["choices"][0]
                        message = choice["message"]
                        row["content"] = message.get("content")
                        if (
                            choice["finish_reason"] != "stop"
                            or not isinstance(row["content"], str)
                            or message.get("tool_calls")
                            or message.get("refusal")
                        ):
                            row["error"] = "invalid_completion"
                    except (KeyError, IndexError, TypeError):
                        row["error"] = "invalid_completion"
                write_new(result, row)
                self.finished += 1
                if self.finished % 25 == 0 or row["error"]:
                    print(
                        json.dumps(
                            {
                                "finished_this_process": self.finished,
                                "label": label,
                                "error": row["error"],
                            }
                        ),
                        flush=True,
                    )
                if row["error"] in {"http_401", "http_403"}:
                    raise RuntimeError("provider authorization failed; execution stopped")
            if row["error"] not in RETRY or attempt == 2:
                return row
            if fresh:
                await asyncio.sleep(10 * (attempt + 1))
        raise AssertionError("unreachable")


async def pool(jobs, worker, concurrency=32):
    iterator = iter(jobs)

    async def consume():
        for job in iterator:
            await worker(job)

    await asyncio.gather(*(consume() for _ in range(concurrency)))
