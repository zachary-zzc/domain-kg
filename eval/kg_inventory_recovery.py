"""Explicit post-hoc recovery of an unused field in inventory verification.

Reference candidates themselves have no reference-to-reference coverage mapping.
The judge filled that unused field with quotations despite the empty-list prompt.
Preserve all original verdicts, reasons and raw responses; do not relax candidate
fact matching, source validation, ID completeness or judgment schemas.
"""

import argparse
import hashlib
import json
from pathlib import Path

from eval.dual_path_data import json_object
from eval.kg_comparison import ROOT, parse_fact_audit
from eval.source_role_eval import FactJudgment, digest
from eval.source_role_run import read, write_new


def parse_inventory_check(raw, ids):
    value = json_object(raw)
    if not isinstance(value, dict) or set(value) != {"facts"}:
        raise ValueError("invalid inventory judgment envelope")
    if not isinstance(value["facts"], list):
        raise ValueError("invalid inventory judgment list")
    facts = [FactJudgment.model_validate(f).model_dump() for f in value["facts"]]
    normalized = [{**f, "matches": []} for f in facts]
    return parse_fact_audit(json.dumps({"facts": normalized}), ids, set())


def derive(root, doc):
    original = read(root / "inventory-checks" / (doc["id"] + ".json"))
    if original["error"] != "invalid_judgment":
        return {"original_sha256": digest(original), "record": original, "normalized": False}
    response = read(
        root / "inventory-check-calls" / (digest(original["call_label"]) + ".result.json")
    )
    if response["error"]:
        raise ValueError("protocol recovery cannot replace transport failure")
    inventory = read(root / "inventories" / (doc["id"] + ".json"))
    if inventory["error"]:
        raise ValueError("protocol recovery cannot repair missing source inventory")
    try:
        parsed = parse_inventory_check(
            response["content"], {r["id"] for r in inventory["references"]}
        )
        result = {**original, **parsed, "error": None}
    except ValueError:
        result = original
    return {
        "original_sha256": digest(original),
        "response_sha256": digest(response),
        "record": result,
        "normalized": result["error"] is None,
    }


def inventory_check(root, doc):
    path = root / "inventory-checks-v2" / (doc["id"] + ".json")
    if not path.exists():
        return read(root / "inventory-checks" / (doc["id"] + ".json"))
    row = read(path)
    if row != derive(root, doc):
        raise ValueError("inventory recovery differs from the immutable raw response")
    return row["record"]


def recover(root):
    manifest = {
        "version": "inventory-unused-matches-v2",
        "timing": "post-hoc protocol fix after all constructors and fact judges completed",
        "reason": "55 checks rejected: the unused matches field held quotations",
        "change": "validate schema; clear unused matches; preserve IDs, verdicts and reasons",
        "scope": "inventory verification only; candidate semantic coverage mappings remain strict",
        "api_calls": 0,
        "originals_preserved": True,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    path = root / "inventory-recovery-amendment.json"
    if path.exists():
        if read(path) != manifest:
            raise ValueError("inventory recovery code changed after amendment")
    else:
        write_new(path, manifest)
    recovered = 0
    for doc in read(root / "sources.json"):
        row = derive(root, doc)
        target = root / "inventory-checks-v2" / (doc["id"] + ".json")
        if target.exists():
            if read(target) != row:
                raise ValueError("inventory recovery output changed")
        else:
            write_new(target, row)
        recovered += row["normalized"]
    print(json.dumps({"sources": 64, "recovered": recovered, "api_calls": 0}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    recover(parser.parse_args().root)
