"""Full-corpus direct-KG controls using the unchanged production retrieval/scorers."""

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import httpx

from domain_kg.retrieval.ranking import BM25
from eval.kg_comparison import CLOUD, PREVIOUS, ROOT
from eval.scale_guard import Runner, pool
from eval.scale_reasoned import MODEL, PRICE, reasoned
from eval.scale_retrieval import (
    blind_request,
    code_hashes,
    context_size,
    evaluation_id,
    pack,
    parse_blind,
    parse_reader,
    parse_semantic,
    reader_request,
    semantic_request,
)
from eval.scale_transport import PRICES, RETRY
from eval.scoped_retrieval import OfflineIndex, evidence_coverage
from eval.source_role_eval import digest
from eval.source_role_run import credential, read, write_new

ARMS = {
    **{"KG3+RAG--" + m: (m, True) for m in CLOUD},
    "KG3-only--qwen3.6-35b-a3b": ("qwen3.6-35b-a3b", False),
}


def hashes():
    return {**code_hashes(), __file__: hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def old_call(stage, payload):
    directory = "evaluation-calls" if stage == "reader" else "reasoned-calls"
    for attempt in range(3):
        label = stage + "/" + digest(payload) + ":" + str(attempt)
        path = PREVIOUS / directory / (digest(label) + ".result.json")
        launch = PREVIOUS / directory / (digest(label) + ".launch.json")
        if not launch.exists():
            return None
        old = read(launch)
        if old["request_sha256"] != digest(payload):
            raise ValueError("prior request integrity mismatch")
        if path.exists():
            row = read(path)
            if row["error"] in RETRY and attempt < 2:
                continue
            return {**row, "reused_from": str(path)}
        return {
            "error": "ambiguous_launch",
            "content": None,
            "label": label,
            "reused_from": str(launch),
        }
    return None


async def prepare(root):
    docs, cases, ranks, initial = (
        read(PREVIOUS / (n + ".json")) for n in ("corpus", "cases", "ranks", "initial")
    )
    frozen = {
        "code": hashes(),
        "inputs": {
            n: digest(v)
            for n, v in zip(
                ("corpus", "cases", "ranks", "initial"), (docs, cases, ranks, initial), strict=True
            )
        },
        "arms": ARMS,
        "reader": "qwen3.6-35b-a3b",
        "judge": MODEL,
        "max_content_chars": 14000,
        "top_k": 12,
        "kg_only": (
            "35B representative fixed before scoring; no vector/text candidates or vector seeds"
        ),
        "construction": (
            "Existing direct batched 3-fact KG; compare against existing "
            "context-corrected 8-fact KG. Not a capacity-matched correction ablation."
        ),
    }
    frozen_path = root / "retrieval-frozen.json"
    if frozen_path.exists():
        if digest(read(frozen_path)) != digest(frozen):
            raise ValueError("retrieval changed")
    else:
        write_new(frozen_path, frozen)
    bm25 = BM25({d["id"]: d["title"] + "\n" + d["text"] for d in docs})
    for arm, (model, hybrid) in ARMS.items():
        target = root / "packets" / (arm + ".json")
        if target.exists():
            continue
        facts = [{"d": doc, **f} for doc, values in initial[model].items() for f in values]
        index = OfflineIndex(
            docs, facts, kg_id=uuid5(NAMESPACE_URL, "full-scale-retrieval-v1"), bm25=bm25
        )
        packets = []
        for case, vector_ids in zip(cases, ranks, strict=True):
            start = time.monotonic()
            branches, view = await index.retrieve(case["question"], vector_ids if hybrid else [])
            if not hybrid:
                branches = {
                    k: v if k not in ("vector", "lexical") else [] for k, v in branches.items()
                }
            evidence, selected = pack(index, branches, view, graph=True)
            packet = {
                k: case[k]
                for k in (
                    "id",
                    "dataset",
                    "question",
                    "answers",
                    "answerable",
                    "stratum",
                    "difficulty",
                )
            }
            packet.update(
                reference_sources=[s for s in case["contexts"] if s["id"] in case["support"]],
                evidence=evidence,
                diagnostics={
                    "reference_coverage": evidence_coverage(case, selected, index),
                    "graph_truncation": view.truncation,
                    "retrieval_seconds": time.monotonic() - start,
                    "source_count": len(selected),
                    "content_chars": context_size(evidence),
                },
            )
            packets.append(packet)
        write_new(target, packets)
        write_new(
            root / "packets" / (arm + ".manifest.json"),
            {
                "sha256": digest(packets),
                "facts": len(facts),
                "sources_with_facts": len({f["d"] for f in facts}),
                "questions": len(packets),
            },
        )
        print(json.dumps({"prepared": arm, "questions": len(packets)}), flush=True)


async def execute(root, key, concurrency=32):
    if read(root / "retrieval-frozen.json")["code"] != hashes():
        raise ValueError("retrieval code changed")
    PRICES[MODEL] = PRICE
    runner = Runner(root / "retrieval-calls", key)
    jobs = {evaluation_id(p): p for arm in ARMS for p in read(root / "packets" / (arm + ".json"))}
    locks = {}
    async with httpx.AsyncClient(
        timeout=None, limits=httpx.Limits(max_connections=concurrency)
    ) as client:

        async def call(stage, payload):
            async with locks.setdefault(digest(payload), asyncio.Lock()):
                return old_call(stage, payload) or await runner.call(
                    client, stage + "/" + digest(payload), payload
                )

        async def worker(item):
            ident, packet = item
            output = root / "retrieval-results" / (ident + ".json")
            if output.exists():
                return
            prior = PREVIOUS / "reasoned" / (ident + ".json")
            if prior.exists():
                write_new(output, {**read(prior), "reused_evaluation": str(prior)})
                return
            sources = {s["id"] for s in packet["evidence"]["sources"]}
            row = await call("reader", reader_request(packet))
            reader = parse_reader(row["content"], sources)
            result = {
                "id": ident,
                "case_id": packet["id"],
                "judge": MODEL,
                "reader": reader,
                "blind": None,
                "semantic": None,
                "errors": {},
                "calls": {"reader": row["label"]},
                "reused_calls": {},
            }
            if row.get("reused_from"):
                result["reused_calls"]["reader"] = row["reused_from"]
            if row["error"]:
                result["errors"]["reader"] = row["error"]
            for stage in ("blind", "semantic"):
                payload = reasoned(
                    blind_request(packet, reader)
                    if stage == "blind"
                    else semantic_request(packet, reader, result["blind"])
                )
                row = await call("reasoned-" + stage, payload)
                result["calls"][stage] = row["label"]
                if row.get("reused_from"):
                    result["reused_calls"][stage] = row["reused_from"]
                try:
                    if row["error"]:
                        raise ValueError(row["error"])
                    result[stage] = (
                        parse_blind(row["content"], sources, bool(packet["evidence"]["paths"]))
                        if stage == "blind"
                        else parse_semantic(row["content"])
                    )
                except ValueError:
                    result["errors"][stage] = row["error"] or "invalid_judgment"
            write_new(output, result)

        await pool(jobs.items(), worker, concurrency)
    print(json.dumps({"complete": len(jobs), "arms": list(ARMS)}), flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["prepare", "run"])
    p.add_argument("--root", type=Path, default=ROOT)
    p.add_argument(
        "--key-file", type=Path, default=Path("/private/tmp/domain-kg-full-scale-api-key")
    )
    a = p.parse_args()
    asyncio.run(prepare(a.root) if a.mode == "prepare" else execute(a.root, credential(a.key_file)))


if __name__ == "__main__":
    main()
