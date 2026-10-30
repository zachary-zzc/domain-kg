"""Single-source correction protocol after the excluded batch omission pilot."""

import argparse
import asyncio
import json
from pathlib import Path

import httpx

from eval.public_qa_data import MODELS
from eval.scale_construction import graph_hints
from eval.scale_construction import hashes as batch_hashes
from eval.scale_transport import Runner, pool
from eval.source_role_eval import digest
from eval.source_role_expand import repair_request, validated_facts
from eval.source_role_run import credential, read, run_lock, write_new

ROOT = Path(".cache/full-scale-retrieval-2026-10-21-v2")


def single_request(model, doc, proposals, related, hints):
    payload = repair_request(doc, proposals, related, hints)
    payload["model"] = model
    if model.startswith("deepseek"):
        payload.pop("enable_thinking")
        payload.pop("response_format")
    return payload


def hashes():
    import hashlib

    return {**batch_hashes(), __file__: hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def prepare(root, previous):
    if root.exists():
        raise ValueError("experiment exists")
    prior = read(previous / "construction-frozen.json")
    root.mkdir(parents=True)
    for name, expected in prior["inputs"].items():
        value = read(previous / (name + ".json"))
        if digest(value) != expected:
            raise ValueError("prior input changed")
        write_new(root / (name + ".json"), value)
    write_new(
        root / "construction-frozen.json",
        {
            **prior,
            "code": hashes(),
            "excluded_batch_pilot": str(previous),
            "batch_size": 1,
            "max_output_tokens": 4096,
            "qualification": (
                "Four synthetic source-only fixtures; at least 3 with grounded nonempty facts"
            ),
        },
    )


def verify(root):
    frozen = read(root / "construction-frozen.json")
    if frozen["code"] != hashes():
        raise ValueError("single-source correction code changed")
    for name, expected in frozen["inputs"].items():
        if digest(read(root / (name + ".json"))) != expected:
            raise ValueError("single-source correction inputs changed")


async def execute(root, key, *, qualify=False, concurrency=32):
    verify(root)
    runner = Runner(root / "calls", key)
    if qualify:
        docs = [
            {
                "id": "fixture-" + str(i),
                "title": "Team " + a,
                "text": f"Team {a} uses Device {b}. Team {a} does not use Device Zeta.",
            }
            for i, (a, b) in enumerate(
                [("Alpha", "Beta"), ("Gamma", "Delta"), ("Epsilon", "Theta"), ("Iota", "Kappa")]
            )
        ]
        initial, related = {m: {} for m in MODELS}, {d["id"]: [] for d in docs}
        models, directory = MODELS, "qualification"
    else:
        docs = read(root / "corpus.json")
        initial, related = read(root / "initial.json"), read(root / "related.json")
        qualification = read(root / "qualification.json")
        models = [m for m in MODELS if qualification[m]["eligible"]]
        directory = "construction"
    async with httpx.AsyncClient(
        timeout=None, limits=httpx.Limits(max_connections=concurrency)
    ) as client:

        async def worker(job):
            doc, model = job
            output = root / directory / model / (doc["id"] + ".json")
            if output.exists():
                return
            hints = graph_hints(doc, related, initial[model])
            payload = single_request(
                model, doc, initial[model].get(doc["id"], []), related[doc["id"]], hints
            )
            row = await runner.call(client, f"{directory}/{model}/{doc['id']}", payload)
            try:
                if row["error"]:
                    raise ValueError(row["error"])
                record = {**validated_facts(row["content"], doc, 8), "error": None}
            except ValueError:
                record = {
                    "facts": [],
                    "rejections": [],
                    "raw_count": None,
                    "error": row["error"] or "invalid_source_facts",
                }
            write_new(
                output,
                {
                    "model": model,
                    "document": doc["id"],
                    **record,
                    "call_label": row["label"],
                    "request_sha256": digest(payload),
                },
            )

        await pool(((d, m) for d in docs for m in models), worker, concurrency)
    if qualify:
        results = {}
        for model in MODELS:
            rows = [read(root / directory / model / (d["id"] + ".json")) for d in docs]
            valid = sum(not r["error"] and bool(r["facts"]) for r in rows)
            results[model] = {
                "eligible": valid >= 3,
                "valid_nonempty": valid,
                "attempted": len(rows),
            }
        write_new(root / "qualification.json", results)
        print(json.dumps(results), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "qualify", "run"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--previous", type=Path, default=Path(".cache/full-scale-retrieval-2026-10-21-v1")
    )
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--concurrency", type=int, default=32)
    args = parser.parse_args()
    if args.mode == "prepare":
        prepare(args.root, args.previous)
    else:
        with run_lock(args.root):
            asyncio.run(
                execute(
                    args.root,
                    credential(args.key_file),
                    qualify=args.mode == "qualify",
                    concurrency=args.concurrency,
                )
            )


if __name__ == "__main__":
    main()
