"""API-only Llama supplement with frozen prompts and resumable free-tier limits."""

import argparse
import asyncio
import hashlib
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from eval.kg_comparison import ARMS, PREVIOUS, ROOT, construction_request, fact_record, verify
from eval.public_qa_run import bounded_call
from eval.scale_construction import graph_hints
from eval.source_role_eval import digest
from eval.source_role_run import credential, read, write_new

MODELS = {
    "cf-llama-3.2-1b": "@cf/meta/llama-3.2-1b-instruct",
    "cf-llama-3.2-3b": "@cf/meta/llama-3.2-3b-instruct",
    "cf-llama-3.1-8b-fp8": "@cf/meta/llama-3.1-8b-instruct-fp8-fast",
}
# Official list prices, checked 2026-10-21; not an account invoice.
NEURONS = dict(zip(MODELS.values(), ((2457, 18252), (4625, 30475), (4119, 34868)), strict=True))
USD = dict(zip(MODELS.values(), ((0.027, 0.201), (0.051, 0.335), (0.045, 0.384)), strict=True))
DAILY_ALLOWANCE = 9500  # Leaves room for the synthetic probes and other account activity.


class QuotaPause(RuntimeError):
    pass


def endpoint(account):
    if not re.fullmatch(r"[a-f0-9]{32}", account):
        raise ValueError("invalid Cloudflare account ID")
    return f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1/chat/completions"


def wire_request(payload):
    if payload["model"] not in MODELS:
        raise ValueError("unapproved Cloudflare model")
    keys = {"model", "messages", "max_tokens", "temperature", "response_format"}
    return {**{k: v for k, v in payload.items() if k in keys}, "model": MODELS[payload["model"]]}


def estimated_reservation(payload):
    pi, po = NEURONS[payload["model"]]
    # Byte count plus message overhead is conservative for this English corpus.
    input_bound = len(json.dumps(payload["messages"], ensure_ascii=False).encode()) + 256
    return (input_bound * pi + payload["max_tokens"] * po) / 1_000_000


def prepare(root, account):
    verify(root)
    path = root / "cloudflare-supplement-frozen.json"
    manifest = {
        "original_manifest_sha256": digest(read(root / "frozen.json")),
        "sources_sha256": digest(read(root / "sources.json")),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "models": MODELS,
        "account_id": account,
        "endpoint": endpoint(account),
        "arms": list(ARMS),
        "max_tokens": 1536,
        "api_only": True,
        "daily_neuron_allowance": DAILY_ALLOWANCE,
        "selection": "Late supplement after three synthetic API probes, before corpus outputs",
        "transport": "Same messages and output budget; remove Alibaba enable_thinking/store fields",
        "limits": "No paid plan changes. Stop on auth/quota errors; keep incomplete work pending",
        "price_source": "https://developers.cloudflare.com/workers-ai/platform/pricing/",
        "neurons_per_million": NEURONS,
        "usd_list_price_per_million": USD,
    }
    if path.exists():
        previous = read(path)
        expected = previous
        amendment = root / "cloudflare-code-amendment.json"
        if amendment.exists():
            change = read(amendment)
            if change["original_manifest_sha256"] != digest(previous):
                raise ValueError("Cloudflare amendment refers to another manifest")
            expected = {**previous, "script_sha256": change["script_sha256"]}
        if expected != json.loads(json.dumps(manifest)):
            raise ValueError("Cloudflare supplement changed after freeze")
        return previous
    else:
        write_new(path, manifest)
    return manifest


class Runner:
    def __init__(self, root, key, account):
        self.root, self.key, self.url = root, key, endpoint(account)
        root.mkdir(parents=True, exist_ok=True)
        self.finished = 0

    def day_usage(self, day):
        total = 0.0
        for path in self.root.glob("*.launch.json"):
            row = read(path)
            if row["utc_day"] != day:
                continue
            result = path.with_name(path.name.replace(".launch.", ".result."))
            value = (read(result).get("usage") or {}).get("neurons") if result.exists() else None
            total += (
                value if type(value) in (float, int) and value >= 0 else row["reserved_neurons"]
            )
        return total

    async def call(self, client, label, payload):
        today = datetime.now(UTC).date().isoformat()
        launch = self.root / (digest(label) + ".launch.json")
        result = self.root / (digest(label) + ".result.json")
        if launch.exists():
            old = read(launch)
            if (
                old["request_sha256"] != digest(payload)
                or old["payload"] != payload
                or old["endpoint"] != self.url
            ):
                raise ValueError("Cloudflare checkpoint identity changed")
            if not result.exists():
                return {"label": label, "content": None, "error": "ambiguous_launch"}
            row = read(result)
            if row["error"] == "http_429":
                if row["utc_day"] == today:
                    raise QuotaPause(
                        "Provider rate/quota limit; retain checkpoint until next UTC day"
                    )
                if label.count(":resume:") >= 7:
                    raise QuotaPause("Maximum seven daily quota resumes reached")
                return await self.call(client, label + ":resume:" + today, payload)
            if row["error"] in ("http_401", "http_403"):
                raise RuntimeError("Cloudflare authorization failed; no repeated requests")
            return row
        if payload["model"] not in NEURONS or payload["max_tokens"] != 1536:
            raise ValueError("unapproved Cloudflare model or token budget")
        reserve = estimated_reservation(payload)
        if self.day_usage(today) + reserve > DAILY_ALLOWANCE:
            raise QuotaPause(
                "Daily experimental Neuron allowance reached; unfinished cases preserved"
            )
        write_new(
            launch,
            {
                "label": label,
                "payload": payload,
                "request_sha256": digest(payload),
                "endpoint": self.url,
                "launched_at": time.time(),
                "utc_day": today,
                "reserved_neurons": reserve,
            },
        )
        raw = await bounded_call(client, self.url, payload, self.key, deadline=180)
        response = raw.get("response") or {}
        content, error = None, raw["error"]
        if error is None:
            try:
                choice = response["choices"][0]
                message = choice["message"]
                content = message.get("content")
                if (
                    choice["finish_reason"] != "stop"
                    or not isinstance(content, str)
                    or message.get("tool_calls")
                    or message.get("refusal")
                ):
                    error = "invalid_completion"
            except (KeyError, IndexError, TypeError):
                error = "invalid_completion"
        row = {
            "label": label,
            "model": payload["model"],
            "request_sha256": digest(payload),
            "utc_day": today,
            "content": content,
            "error": error,
            "usage": response.get("usage"),
            "response": response,
            "seconds": raw["seconds"],
            "http_status": raw["http_status"],
        }
        write_new(result, row)
        self.finished += 1
        if self.finished % 10 == 0 or error:
            print(
                json.dumps(
                    {
                        "finished": self.finished,
                        "model": payload["model"],
                        "error": error,
                        "daily_neurons": self.day_usage(today),
                    }
                ),
                flush=True,
            )
        if error in ("http_401", "http_403"):
            raise RuntimeError("Cloudflare authorization failed; execution stopped")
        if error == "http_429":
            raise QuotaPause("Provider rate/quota limit; unfinished cases preserved")
        return row


async def construct(root, key, account):
    prepare(root, account)
    sources, related = read(root / "sources.json"), read(PREVIOUS / "related.json")
    runner = Runner(root / "cloudflare-construction-calls", key, account)
    async with httpx.AsyncClient(timeout=None, follow_redirects=False) as client:
        for arm in ARMS:
            direct = (
                {
                    model: {
                        d["id"]: read(
                            root / "construction" / model / "direct" / (d["id"] + ".json")
                        )["facts"]
                        for d in sources
                    }
                    for model in MODELS
                }
                if arm != "direct"
                else {}
            )
            # One request at a time; interleave model sizes on each original source.
            for doc in sources:
                for model in MODELS:
                    proposals = direct.get(model, {}).get(doc["id"], [])
                    hints = (
                        graph_hints(doc, related, direct.get(model, {})) if arm != "direct" else []
                    )
                    payload = wire_request(
                        construction_request(model, doc, arm, proposals, related[doc["id"]], hints)
                    )
                    output = root / "construction" / model / arm / (doc["id"] + ".json")
                    if output.exists():
                        if read(output)["request_sha256"] != digest(payload):
                            raise ValueError("Cloudflare construction checkpoint changed")
                        continue
                    row = await runner.call(
                        client, "cloudflare-construct/" + digest(payload), payload
                    )
                    write_new(
                        output,
                        {
                            "model": model,
                            "arm": arm,
                            "document": doc["id"],
                            "request_sha256": digest(payload),
                            "call_label": row["label"],
                            **fact_record(row, doc),
                        },
                    )


def accounting(root):
    rows = []
    for alias, model in MODELS.items():
        launches = [
            read(p)
            for p in (root / "cloudflare-construction-calls").glob("*.launch.json")
            if read(p)["payload"]["model"] == model
        ]
        results = []
        for launch in launches:
            path = (
                root / "cloudflare-construction-calls" / (digest(launch["label"]) + ".result.json")
            )
            if path.exists():
                results.append(read(path))
        usages = [r["usage"] for r in results if isinstance(r.get("usage"), dict)]
        tokens = [
            (u["prompt_tokens"], u["completion_tokens"])
            for u in usages
            if all(
                type(u.get(k)) is int and u[k] >= 0 for k in ("prompt_tokens", "completion_tokens")
            )
        ]
        rows.append(
            {
                "provider": "Cloudflare",
                "model": alias,
                "wire_model": model,
                "attempts": len(launches),
                "reported_neurons": sum(u.get("neurons", 0) for u in usages),
                "unknown_neuron_attempts": len(launches)
                - sum(type(u.get("neurons")) in (int, float) for u in usages),
                "prompt_tokens": sum(v[0] for v in tokens),
                "completion_tokens": sum(v[1] for v in tokens),
                "list_price_usd_equivalent": sum(
                    a * USD[model][0] + b * USD[model][1] for a, b in tokens
                )
                / 1_000_000,
                "billed_usd": None,
                "note": "Free quota may apply; USD equivalent is not an invoice",
            }
        )
    return rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["prepare", "run"])
    p.add_argument("--root", type=Path, default=ROOT)
    p.add_argument("--account-id", required=True)
    p.add_argument(
        "--key-file", type=Path, default=Path("/private/tmp/domain-kg-cloudflare-api-key")
    )
    args = p.parse_args()
    if args.mode == "prepare":
        prepare(args.root, args.account_id)
        print("Cloudflare supplement frozen")
    else:
        try:
            asyncio.run(construct(args.root, credential(args.key_file), args.account_id))
        except QuotaPause as exc:
            print(str(exc), flush=True)
            raise SystemExit(75) from exc


if __name__ == "__main__":
    main()
