"""Complete same-model RAG-assisted original-source correction; offline only."""

import argparse
import asyncio
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import httpx

from domain_kg.retrieval.ranking import BM25
from eval.dual_path_data import batches, json_object
from eval.public_qa_data import MODELS
from eval.scale_transport import Runner, pool
from eval.scoped_retrieval_run import load_saved, saved_vector_ranks
from eval.source_role_eval import digest, source_only
from eval.source_role_expand import repair_request, validated_facts
from eval.source_role_run import credential, read, run_lock, write_new

ORIGINAL = Path(".cache/dual-path-2026-10-20-v3a")
ROOT = Path(".cache/full-scale-retrieval-2026-10-21-v1")
CODE = [
    "eval/scale_construction.py",
    "eval/scale_transport.py",
    "eval/source_role_expand.py",
    "eval/source_role_eval.py",
    "eval/source_role_run.py",
    "eval/public_qa_run.py",
    "eval/dual_path_data.py",
    "src/domain_kg/providers/semantics.py",
    "src/domain_kg/retrieval/ranking.py",
]


def hashes():
    return {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in CODE}


def related_sources(docs):
    by_id = {d["id"]: d for d in docs}
    index = BM25({d["id"]: d["title"] + "\n" + d["text"] for d in docs})
    result = {}
    for doc in docs:
        scores = index.scores(doc["title"][:500])
        chosen, remaining = [], 2000
        for ident in sorted(scores, key=lambda i: (-scores[i], i)):
            other = by_id[ident]
            if ident == doc["id"] or other["dataset"] != doc["dataset"]:
                continue
            if not remaining or len(chosen) == 2:
                break
            text = other["text"][:remaining]
            chosen.append({**source_only(other), "text": text})
            remaining -= len(text)
        result[doc["id"]] = chosen
    return result


def correction_request(model, docs, proposals, related, hints):
    template = repair_request(docs[0], [])
    instructions = template["messages"][0]["content"]
    instructions += (
        "\nProcess EACH item independently, up to EIGHT facts PER item. Output ONLY "
        '{"items":[{"id":"s0","facts":[...]},{"id":"s1","facts":[...]}]}. '
        "Each facts array uses the fact schema above. Include every input item exactly once, "
        "including an empty facts array when appropriate. Source IDs are s0, s1, etc. "
        "Never mix an item's primary source with another item or related sources. "
        "No commentary or reasoning outside the JSON."
    )
    template["model"], template["max_tokens"] = model, 8192
    template["messages"] = [
        {"role": "system", "content": instructions},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "items": [
                        {
                            "id": f"s{i}",
                            "source": source_only(d),
                            "proposals": proposals.get(d["id"], []),
                            "related_sources": [source_only(s) for s in related.get(d["id"], [])],
                            "graph_hints": hints.get(d["id"], []),
                        }
                        for i, d in enumerate(docs)
                    ]
                },
                ensure_ascii=False,
            ),
        },
    ]
    if model.startswith("deepseek"):
        template.pop("enable_thinking")
        template.pop("response_format")
    return template


def parse_correction(raw, docs):
    value = json_object(raw)
    grouped = defaultdict(list)
    envelope_ok = isinstance(value, dict) and set(value) == {"items"}
    envelope_ok = envelope_ok and isinstance(value["items"], list)
    if envelope_ok:
        for item in value["items"]:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                grouped[item["id"]].append(item)
    result = {}
    for i, doc in enumerate(docs):
        items = grouped[f"s{i}"]
        record = {"facts": [], "rejections": [], "raw_count": None, "error": None}
        if not envelope_ok:
            record["error"] = "invalid_batch_envelope"
        elif len(items) != 1:
            record["error"] = "missing_or_duplicate_source"
        else:
            try:
                if set(items[0]) != {"id", "facts"}:
                    raise ValueError("extra fields")
                record.update(validated_facts(json.dumps({"facts": items[0]["facts"]}), doc, 8))
            except ValueError:
                record["error"] = "invalid_source_facts"
        result[doc["id"]] = record
    return result


def graph_hints(doc, related, proposals):
    labels = {f[k].casefold() for f in proposals.get(doc["id"], []) for k in ("s", "o")}
    return [
        {"source_id": other["id"], **f}
        for other in related[doc["id"]]
        for f in proposals.get(other["id"], [])
        if {f["s"].casefold(), f["o"].casefold()} & labels and f["q"] in other["text"]
    ][:8]


def prepare(root, original=ORIGINAL):
    if root.exists():
        raise ValueError("experiment already exists")
    from eval.dual_path_run import verify as verify_original

    verify_original(original, "formal-frozen.json")
    docs, cases = read(original / "corpus.json"), read(original / "cases.json")
    if len(docs) != 6566 or len(cases) != 1000:
        raise ValueError("incomplete corpus")
    rows, inputs = load_saved(original)
    ranks = saved_vector_ranks(rows, docs, cases, read(original / "vector-ranks.json"))
    initial = {}
    for model in MODELS:
        model_rows = [r for r in rows if r["kind"] == "formal_graph" and r["model"] == model]
        if len(model_rows) != 938:
            raise ValueError("incomplete initial construction attempts")
        grouped = defaultdict(list)
        for row in model_rows:
            for fact in (row.get("graph") or {}).get("facts", []):
                grouped[fact["d"]].append({k: v for k, v in fact.items() if k != "d"})
        initial[model] = dict(grouped)
    related = related_sources(docs)
    root.mkdir(parents=True)
    for name, data in (
        ("corpus", docs),
        ("cases", cases),
        ("initial", initial),
        ("related", related),
        ("ranks", ranks),
    ):
        write_new(root / (name + ".json"), data)
    write_new(
        root / "construction-frozen.json",
        {
            "code": hashes(),
            "models": MODELS,
            "original": str(original),
            "original_inputs": inputs,
            "inputs": {
                name: digest(read(root / (name + ".json")))
                for name in ("corpus", "cases", "initial", "related", "ranks")
            },
            "limits": {"calls": None, "rmb": None, "authorization": "user lifted both limits"},
            "batch_size": 4,
            "batch_primary_chars": 4400,
            "max_facts_per_source": 8,
            "max_output_tokens": 8192,
            "temperature": 0,
            "audit_case_ids": [
                c["id"]
                for dataset in sorted({c["dataset"] for c in cases})
                for c in sorted(
                    (c for c in cases if c["dataset"] == dataset),
                    key=lambda c: digest(["audit-v1", c["id"]]),
                )[:25]
            ],
        },
    )
    print(
        json.dumps(
            {
                "sources": len(docs),
                "cases": len(cases),
                "batches_per_model": len(batches(docs, max_docs=4)),
            }
        )
    )


def verify(root):
    frozen = read(root / "construction-frozen.json")
    if frozen["code"] != hashes():
        raise ValueError("construction code changed")
    for name, expected in frozen["inputs"].items():
        if digest(read(root / (name + ".json"))) != expected:
            raise ValueError("construction input changed")


async def execute(root, key, limit=None, concurrency=32):
    verify(root)
    docs = read(root / "corpus.json")
    initial, related = read(root / "initial.json"), read(root / "related.json")
    grouped = batches(docs, max_docs=4)
    runner = Runner(root / "calls", key)
    async with httpx.AsyncClient(
        timeout=None, limits=httpx.Limits(max_connections=concurrency)
    ) as client:

        async def worker(job):
            batch_id, model = job
            output = root / "construction" / model / f"{batch_id:05}.json"
            if output.exists():
                return
            batch = grouped[batch_id]
            hints = {d["id"]: graph_hints(d, related, initial[model]) for d in batch}
            payload = correction_request(model, batch, initial[model], related, hints)
            row = await runner.call(client, f"correction/{model}/{batch_id}", payload)
            records = parse_correction(row.get("content") if not row["error"] else None, batch)
            write_new(
                output,
                {
                    "model": model,
                    "batch": batch_id,
                    "documents": records,
                    "call_label": row["label"],
                    "request_sha256": digest(payload),
                    "transport_error": row["error"],
                },
            )

        jobs = [
            (i, m)
            for i in range(len(grouped) if limit is None else min(limit, len(grouped)))
            for m in MODELS
        ]
        await pool(jobs, worker, concurrency)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "run"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--limit-batches", type=int)
    parser.add_argument("--concurrency", type=int, default=32)
    args = parser.parse_args()
    if args.mode == "prepare":
        prepare(args.root)
    else:
        with run_lock(args.root):
            asyncio.run(
                execute(args.root, credential(args.key_file), args.limit_batches, args.concurrency)
            )


if __name__ == "__main__":
    main()
