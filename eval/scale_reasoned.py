"""Postregistered uniform reasoning-enabled judge; original judgments are retained."""

import argparse
import asyncio
import copy
import hashlib
import json
from pathlib import Path

import httpx

from eval.scale_guard import Runner, pool
from eval.scale_retrieval import (
    blind_request,
    code_hashes,
    evaluation_id,
    parse_blind,
    parse_semantic,
    semantic_request,
)
from eval.scale_single import ROOT
from eval.scale_transport import PRICES
from eval.source_role_eval import digest
from eval.source_role_run import credential, read, run_lock, write_new

MODEL = "qwen3.8-2.4t-a95b"
PRICE = (12.0, 36.0)


def reasoned(payload):
    value = copy.deepcopy(payload)
    value.update(model=MODEL, enable_thinking=True, thinking_budget=4096, max_tokens=8192)
    # JSON instructions still apply; do not depend on JSON-mode/thinking compatibility.
    value.pop("response_format", None)
    value["messages"][0]["content"] += (
        " Before finalizing, check that each categorical label agrees with your own "
        "explanation. Apply the permitted ordinary inferences consistently. Explicit "
        "before/after constraints and named semantic roles still matter."
    )
    return value


def manifest(root):
    value = {
        "model": MODEL,
        "prices_rmb_per_million": PRICE,
        "thinking_budget": 4096,
        "max_tokens": 8192,
        "scope": "all packets, uniform independent re-evaluation",
        "reason": "native fixed audit found contradictory verdicts and rejected direct inference",
        "code": {
            **code_hashes(),
            __file__: hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
        "price_source": "https://help.aliyun.com/zh/model-studio/qwen3-8-2-4t-a95b",
    }
    path = root / "reasoned-frozen.json"
    if path.exists():
        # JSON represents tuples as lists.
        if digest(read(path)) != digest(value):
            raise ValueError("reasoned judge protocol changed")
    else:
        write_new(path, value)


def fixtures():
    specifications = [
        (
            "ordinal",
            "Nira is the fifth daughter of Oren.",
            "How many older sisters does Nira have?",
            "4",
            [["four"]],
            "sufficient",
            "supported",
            "correct",
        ),
        (
            "time",
            "The orchard was untouched before 1990. Controlled burning was introduced in 1990.",
            "What method cleared this orchard in 1980?",
            "Controlled burning",
            [],
            "sufficient",
            "unsupported",
            "incorrect",
        ),
        (
            "alias",
            "Harbor Works (HW) was founded in Cedar Bay.",
            "Where was HW founded?",
            "Cedar Bay",
            [["Cedar Bay municipality"]],
            "sufficient",
            "supported",
            "correct",
        ),
        (
            "role",
            "Maya performed Bloom. Neri composed Bloom.",
            "Who composed Bloom?",
            "Maya",
            [["Neri"]],
            "sufficient",
            "unsupported",
            "incorrect",
        ),
    ]
    result = []
    for ident, text, question, answer, gold, sufficiency, support, semantic in specifications:
        packet = {
            "id": ident,
            "question": question,
            "answers": gold,
            "reference_sources": [{"id": "s", "text": text}],
            "evidence": {"sources": [{"id": "s", "text": text}], "paths": []},
        }
        reader = {
            "answer": answer,
            "citations": ["s"],
            "abstain": False,
            "protocol_valid": True,
            "invalid_citations": [],
        }
        result.append(
            (
                packet,
                reader,
                {"sufficiency": sufficiency, "reader_support": support, "semantic": semantic},
            )
        )
    return result


async def execute(root, key, arms, *, calibrate=False, concurrency=16):
    manifest(root)
    PRICES[MODEL] = PRICE
    runner = Runner(root / "reasoned-calls", key)
    directory = "reasoned-calibration" if calibrate else "reasoned"
    jobs, seen = [], set()
    if calibrate:
        jobs = fixtures()
    else:
        calibration = read(root / "reasoned-calibration.json")
        if not calibration["passed"]:
            raise ValueError("judge calibration failed")
        for arm in arms:
            packets = read(root / "packets" / (arm + ".json"))
            if digest(packets) != read(root / "packets" / (arm + ".manifest.json"))["sha256"]:
                raise ValueError("packet integrity failure")
            for packet in packets:
                ident = evaluation_id(packet)
                if ident not in seen:
                    original = read(root / "evaluations" / (ident + ".json"))
                    jobs.append((packet, original["reader"], None))
                    seen.add(ident)
    async with httpx.AsyncClient(
        timeout=None, limits=httpx.Limits(max_connections=concurrency)
    ) as client:

        async def worker(job):
            packet, reader, expected = job
            ident = evaluation_id(packet)
            output = root / directory / (ident + ".json")
            if output.exists():
                return
            result = {
                "id": ident,
                "case_id": packet["id"],
                "judge": MODEL,
                "reader": reader,
                "blind": None,
                "semantic": None,
                "errors": {},
                "calls": {},
            }
            payload = reasoned(blind_request(packet, reader))
            row = await runner.call(client, "reasoned-blind/" + digest(payload), payload)
            result["calls"]["blind"] = row["label"]
            try:
                if row["error"]:
                    raise ValueError(row["error"])
                result["blind"] = parse_blind(
                    row["content"],
                    {s["id"] for s in packet["evidence"]["sources"]},
                    bool(packet["evidence"]["paths"]),
                )
            except ValueError:
                result["errors"]["blind"] = row["error"] or "invalid_judgment"
            payload = reasoned(semantic_request(packet, reader, result["blind"]))
            row = await runner.call(client, "reasoned-semantic/" + digest(payload), payload)
            result["calls"]["semantic"] = row["label"]
            try:
                if row["error"]:
                    raise ValueError(row["error"])
                result["semantic"] = parse_semantic(row["content"])
            except ValueError:
                result["errors"]["semantic"] = row["error"] or "invalid_judgment"
            if expected is not None:
                result["expected"] = expected
                result["passed"] = (
                    bool(result["blind"] and result["semantic"])
                    and all(
                        result["blind"][k] == expected[k] for k in ("sufficiency", "reader_support")
                    )
                    and (
                        expected["semantic"] is None
                        or result["semantic"]["semantic"] == expected["semantic"]
                    )
                )
            write_new(output, result)

        await pool(jobs, worker, concurrency)
    if calibrate:
        outcomes = [read(root / directory / (evaluation_id(p) + ".json")) for p, _, _ in jobs]
        for outcome, (_, _, expected) in zip(outcomes, jobs, strict=True):
            outcome["passed"] = (
                bool(outcome["blind"] and outcome["semantic"])
                and all(
                    outcome["blind"][k] == expected[k] for k in ("sufficiency", "reader_support")
                )
                and (
                    expected["semantic"] is None
                    or outcome["semantic"]["semantic"] == expected["semantic"]
                )
            )
        summary = {
            "passed": all(r["passed"] for r in outcomes),
            "cases": [
                {k: r[k] for k in ("case_id", "passed", "blind", "semantic", "errors")}
                for r in outcomes
            ],
        }
        write_new(root / "reasoned-calibration.json", summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    else:
        print(json.dumps({"completed": "reasoned", "unique_packets": len(jobs)}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("calibrate", "run"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--arms", nargs="+", default=["RAG"])
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=16)
    args = parser.parse_args()
    lock_root = args.root / "reasoned-lock"
    lock_root.mkdir(exist_ok=True)
    with run_lock(lock_root):
        asyncio.run(
            execute(
                args.root,
                credential(args.key_file),
                args.arms,
                calibrate=args.mode == "calibrate",
                concurrency=args.concurrency,
            )
        )


if __name__ == "__main__":
    main()
