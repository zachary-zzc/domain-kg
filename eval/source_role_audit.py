"""Post-registered role-defined semantic audit; never rewrites the original trial.

The original judge accepted publisher credits as authorship. This audit gives all
arms the same predicate definitions, retains semantic equivalence, and uses the
35B judge for every source plus 397B on the previously fixed 16-source subset.
No verdict is used to select construction or retrieval candidates.
"""

import argparse
import asyncio
import hashlib
from pathlib import Path

import httpx

from domain_kg.providers.semantics import role_instructions
from eval.dual_path_data import PREDICATES
from eval.source_role_eval import EXTRACTOR, JUDGE, blind_facts
from eval.source_role_expand import (
    CumulativeLedger,
    compact_judgment_request,
    parse_wide_judgments,
    pool,
    verify,
)
from eval.source_role_review import selection
from eval.source_role_run import content, credential, read, run_lock, write_new


def audit_request(doc, facts, references, model):
    payload = compact_judgment_request(doc, facts, references)
    payload["model"] = model
    payload["messages"][0]["content"] += (
        " Interpret candidate predicates using the definitions below. Equivalent wording "
        "is acceptable; a different actor role or relation is not an equivalent. "
        "Do not broaden a predicate to make it match the reference. A source's "
        "publisher/performer statement alone leaves a creator claim insufficient, "
        "not automatically contradicted. Evaluate the complete candidate assertion "
        "including essential qualifiers. These definitions constrain meaning, "
        "not spelling or exact-match identity." + role_instructions(PREDICATES)
    )
    return payload


async def execute(root, key):
    frozen, docs = verify(root)
    pilot = Path(frozen["pilot"])
    with run_lock(root), run_lock(pilot):
        inputs = {d["id"]: root / "documents" / (d["id"] + ".json") for d in docs}
        if not all(p.exists() for p in inputs.values()):
            raise ValueError("construction is incomplete")
        selected = {d["id"] for d in selection(docs)}
        manifest = {
            "reason": "original judge conflated publisher and creator",
            "trigger_source": "d-04cf38b4dd07fe5707d03967",
            "timing": "post-registered after original judgments began; no changes to candidates",
            "primary_model": EXTRACTOR,
            "secondary_model": JUDGE,
            "secondary_sources": sorted(selected),
            "code_sha256": hashlib.sha256(
                await asyncio.to_thread(Path(__file__).read_bytes)
            ).hexdigest(),
            "construction": {
                i: hashlib.sha256(p.read_bytes()).hexdigest() for i, p in inputs.items()
            },
        }
        path = root / "role-audit-frozen.json"
        if path.exists() and read(path) != manifest:
            raise ValueError("role audit changed")
        if not path.exists():
            write_new(path, manifest)
        ledger = CumulativeLedger(root / "calls", [pilot / "calls"])
        async with httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            timeout=None,
            limits=httpx.Limits(max_connections=8),
        ) as client:

            async def worker(doc):
                original = read(inputs[doc["id"]])
                facts, mapping = blind_facts(
                    doc, {a: r["facts"] for a, r in original["arms"].items()}
                )
                if mapping != original["fact_mapping"]:
                    raise ValueError("fact mapping changed")
                for model in (EXTRACTOR, JUDGE) if doc["id"] in selected else (EXTRACTOR,):
                    path = root / "role-audit" / model / (doc["id"] + ".json")
                    if path.exists():
                        continue
                    payload = audit_request(doc, facts, original["references"], model)
                    response = await ledger.call(
                        client, "role-audit:" + model + ":" + doc["id"], payload, key
                    )
                    result = {"id": doc["id"], "judgments": None, "error": None}
                    try:
                        result["judgments"] = parse_wide_judgments(
                            content(response),
                            set(mapping),
                            {r["id"] for r in original["references"]},
                        )
                    except ValueError:
                        result["error"] = "invalid_or_missing_judgment"
                    write_new(path, result)

            await pool(docs, worker, concurrency=8)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--secret-file", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(execute(args.root, credential(args.secret_file)))


if __name__ == "__main__":
    main()
