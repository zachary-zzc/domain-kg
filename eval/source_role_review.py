"""Fixed second-model audit of construction judgments, sharing the experiment budget."""

import argparse
import asyncio
import hashlib
from pathlib import Path

import httpx

from eval.source_role_eval import EXTRACTOR, blind_facts, digest
from eval.source_role_expand import (
    CumulativeLedger,
    compact_judgment_request,
    parse_wide_judgments,
    pool,
    verify,
)
from eval.source_role_run import content, credential, read, run_lock, write_new


def selection(docs):
    return sorted(docs, key=lambda d: digest(["crossreview-v1", d["id"]]))[:16]


async def execute(root, key):
    frozen, docs = verify(root)
    selected = selection(docs)
    pilot = Path(frozen["pilot"])
    with run_lock(root), run_lock(pilot):
        config = {
            "code_sha256": hashlib.sha256(
                await asyncio.to_thread(Path(__file__).read_bytes)
            ).hexdigest(),
            "model": EXTRACTOR,
            "selection": [d["id"] for d in selected],
            "construction_files": {
                d["id"]: hashlib.sha256(
                    (root / "documents" / (d["id"] + ".json")).read_bytes()
                ).hexdigest()
                for d in selected
            },
        }
        path = root / "cross-review-frozen.json"
        if path.exists() and read(path) != config:
            raise ValueError("cross-review protocol changed")
        if not path.exists():
            write_new(path, config)
        ledger = CumulativeLedger(root / "calls", [pilot / "calls"])
        async with httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            timeout=None,
            limits=httpx.Limits(max_connections=4),
        ) as client:

            async def worker(doc):
                path = root / "cross-review" / (doc["id"] + ".json")
                if path.exists():
                    return
                row = read(root / "documents" / (doc["id"] + ".json"))
                facts, mapping = blind_facts(doc, {a: r["facts"] for a, r in row["arms"].items()})
                if mapping != row["fact_mapping"]:
                    raise ValueError("fact identities changed")
                payload = compact_judgment_request(doc, facts, row["references"])
                payload["model"] = EXTRACTOR
                response = await ledger.call(client, "cross-review:" + doc["id"], payload, key)
                result = {"id": doc["id"], "judgments": None, "error": None}
                try:
                    result["judgments"] = parse_wide_judgments(
                        content(response), set(mapping), {r["id"] for r in row["references"]}
                    )
                except ValueError:
                    result["error"] = "invalid_or_missing_judgment"
                write_new(path, result)

            await pool(selected, worker, concurrency=4)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--secret-file", type=Path)
    args = parser.parse_args()
    asyncio.run(execute(args.root, credential(args.secret_file)))


if __name__ == "__main__":
    main()
