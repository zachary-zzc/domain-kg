"""Explicit schema adaptation, separate from the original small-model cohort."""

import argparse
import asyncio
import hashlib
from pathlib import Path

import httpx

from eval.dual_path_data import PREDICATES
from eval.kg_cloudflare import QuotaPause, Runner, wire_request
from eval.kg_cloudflare import prepare as prepare_cloudflare
from eval.kg_comparison import ARMS, PREVIOUS, ROOT, construction_request, fact_record
from eval.scale_construction import graph_hints
from eval.source_role_eval import Facts, digest
from eval.source_role_run import credential, read, write_new

MODELS = {
    "cf-llama-3.2-3b-schema": "cf-llama-3.2-3b",
    "cf-llama-3.1-8b-fp8-schema": "cf-llama-3.1-8b-fp8",
}


def construction_payload(model, doc, arm, proposals=(), related=(), hints=()):
    if model not in MODELS:
        raise ValueError("unapproved schema-adapted model")
    payload = wire_request(
        construction_request(MODELS[model], doc, arm, proposals, related, hints)
    )
    shape = Facts.model_json_schema()
    shape["$defs"]["Fact"]["properties"]["p"]["enum"] = list(PREDICATES)
    payload["response_format"] = {"type": "json_schema", "json_schema": shape}
    return payload


def prepare(root=ROOT):
    parent = read(root / "cloudflare-supplement-frozen.json")
    prepare_cloudflare(root, parent["account_id"])
    manifest = {
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "parent_manifest_sha256": digest(parent),
        "sources_sha256": digest(read(root / "sources.json")),
        "models": MODELS,
        "arms": list(ARMS),
        "max_tokens": 1536,
        "scope": "Separate post-hoc API adaptation; does not replace original cohort",
        "selection": (
            "After observing format failures and one first-source schema probe per model; "
            "1B explicitly rejected JSON Schema, 3B/8B returned valid envelopes. "
            "The pilot source remains in the cohort; this is exploratory, not held-out evidence."
        ),
        "difference": (
            "Same messages, sources, output budget, relation vocabulary and validators; "
            "response_format adds explicit schema, fact count and existing predicate enum"
        ),
        "construction": "Each adapted model builds its own direct proposals and graph hints",
        "shared_ledger": "cloudflare-construction-calls; shared daily account allowance",
        "billed_plan": "No subscription or paid-plan changes",
    }
    target = root / "cloudflare-schema-frozen.json"
    if target.exists():
        if read(target) != manifest:
            raise ValueError("schema-adaptation protocol changed after freeze")
    else:
        write_new(target, manifest)
    return manifest


async def construct(root, key):
    prepare(root)
    parent = read(root / "cloudflare-supplement-frozen.json")
    sources = read(root / "sources.json")
    related = read(PREVIOUS / "related.json")
    runner = Runner(root / "cloudflare-construction-calls", key, parent["account_id"])
    async with httpx.AsyncClient(timeout=None, follow_redirects=False) as client:
        for arm in ARMS:
            direct = (
                {
                    m: {
                        d["id"]: read(root / "construction" / m / "direct" / (d["id"] + ".json"))[
                            "facts"
                        ]
                        for d in sources
                    }
                    for m in MODELS
                }
                if arm != "direct"
                else {}
            )
            for doc in sources:
                for model in MODELS:
                    payload = construction_payload(
                        model,
                        doc,
                        arm,
                        direct.get(model, {}).get(doc["id"], []),
                        related[doc["id"]],
                        graph_hints(doc, related, direct.get(model, {})) if arm != "direct" else [],
                    )
                    target = root / "construction" / model / arm / (doc["id"] + ".json")
                    if target.exists():
                        if read(target)["request_sha256"] != digest(payload):
                            raise ValueError("schema construction checkpoint changed")
                        continue
                    row = await runner.call(
                        client, "cloudflare-construct/" + digest(payload), payload
                    )
                    write_new(
                        target,
                        {
                            "model": model,
                            "arm": arm,
                            "document": doc["id"],
                            "request_sha256": digest(payload),
                            "call_label": row["label"],
                            **fact_record(row, doc),
                        },
                    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["prepare", "run"])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--key-file", type=Path, default=Path("/private/tmp/domain-kg-cloudflare-api-key")
    )
    args = parser.parse_args()
    if args.mode == "prepare":
        prepare(args.root)
        print("Schema adaptation frozen; original results retained")
    else:
        try:
            asyncio.run(construct(args.root, credential(args.key_file)))
        except QuotaPause as exc:
            print(str(exc), flush=True)
            raise SystemExit(75) from exc


if __name__ == "__main__":
    main()
