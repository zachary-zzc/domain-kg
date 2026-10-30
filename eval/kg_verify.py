"""Read-only replay of the extension's frozen inputs, calls and semantic mappings."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from eval.kg_cloudflare import MODELS as CLOUDFLARE_MODELS
from eval.kg_cloudflare import endpoint, wire_request
from eval.kg_cloudflare_schema import MODELS as SCHEMA_MODELS
from eval.kg_cloudflare_schema import construction_payload as schema_payload
from eval.kg_comparison import (
    ARMS,
    PREVIOUS,
    ROOT,
    audit_request,
    construction_request,
    fact_record,
    parse_fact_audit,
    verify,
)
from eval.kg_fact_audit import candidates, inventory_check_request
from eval.kg_inventory_recovery import derive as derive_inventory_recovery
from eval.kg_retrieval_extension import ARMS as RETRIEVAL_ARMS
from eval.kg_retrieval_extension import hashes
from eval.kg_supplemental import models
from eval.scale_construction import graph_hints
from eval.scale_reasoned import MODEL, reasoned
from eval.scale_retrieval import (
    blind_request,
    evaluation_id,
    parse_blind,
    parse_reader,
    parse_semantic,
    reader_request,
    semantic_request,
)
from eval.scale_verify import fact_key, require, verify_ledger, verify_packet
from eval.source_role_eval import digest, parse_references, reference_request
from eval.source_role_run import read


def reader_call_label(result, ident, previous=PREVIOUS):
    if "reader" in result["calls"]:
        return result["calls"]["reader"]
    require(bool(result.get("reused_evaluation")), "missing reader call")
    # Historical reasoned judgments retain only their two judge calls. Their
    # reader belongs to the original evaluation for exactly the same packet.
    original = read(previous / "evaluations" / (ident + ".json"))
    require(
        original["id"] == ident
        and original["case_id"] == result["case_id"]
        and original["reader"] == result["reader"],
        "original reader identity or answer changed",
    )
    return original["calls"]["reader"]


def initial_fact_keys(facts_by_source):
    """Mirror qualified_paths' empty default for legacy, unqualified facts."""
    return {
        fact_key(source, {**fact, "qualifiers": fact.get("qualifiers", [])})
        for source, facts in facts_by_source.items()
        for fact in facts
    }


def verify_cloudflare_ledger(root):
    folder = root / "cloudflare-construction-calls"
    frozen = read(root / "cloudflare-supplement-frozen.json")
    counts = Counter()
    for path in folder.glob("*.launch.json"):
        launch = read(path)
        payload, label = launch["payload"], launch["label"]
        require(path.name == digest(label) + ".launch.json", "Cloudflare label mismatch")
        require(launch["request_sha256"] == digest(payload), "Cloudflare payload changed")
        require(launch["endpoint"] == endpoint(frozen["account_id"]), "Cloudflare account changed")
        require(payload["model"] in CLOUDFLARE_MODELS.values(), "unapproved Cloudflare model")
        require(payload["max_tokens"] == 1536, "Cloudflare output budget changed")
        require(
            label.split(":resume:")[0] == "cloudflare-construct/" + digest(payload), "bad call ID"
        )
        if ":resume:" in label:
            previous, day = label.rsplit(":resume:", 1)
            earlier = read(folder / (digest(previous) + ".result.json"))
            require(earlier["error"] == "http_429" and earlier["utc_day"] < day, "invalid resume")
            require(launch["utc_day"] == day, "Cloudflare resume day changed")
        result = path.with_name(path.name.replace(".launch.", ".result."))
        if result.exists():
            row = read(result)
            require(row["label"] == label, "Cloudflare result label changed")
            require(row["request_sha256"] == digest(payload), "Cloudflare result identity changed")
            require(row["model"] == payload["model"], "Cloudflare result model changed")
            require(row["usage"] == row["response"].get("usage"), "Cloudflare usage changed")
        else:
            counts["ambiguous_launches"] += 1
        counts["attempts"] += 1
    return dict(counts)


def verify_all(root):
    verify(root)
    counts = Counter()
    sources = read(root / "sources.json")
    source_ids = {d["id"] for d in sources}
    require(len(source_ids) == 64, "incomplete construction cohort")
    require(
        Counter(d["dataset"] for d in sources)
        == {
            "hotpot": 16,
            "musique": 16,
            "drop": 16,
            "squad": 16,
        },
        "source stratification changed",
    )
    related = read(PREVIOUS / "related.json")
    directories = [p for p in root.iterdir() if p.is_dir() and p.name.endswith("-calls")]
    call_directories = [*directories, PREVIOUS / "evaluation-calls", PREVIOUS / "reasoned-calls"]

    def call(label, payload):
        for folder in call_directories:
            launch = folder / (digest(label) + ".launch.json")
            if not launch.exists():
                continue
            value = read(launch)
            require(value["request_sha256"] == digest(payload), "call request mismatch")
            require(digest(value["payload"]) == digest(payload), "call payload changed")
            result = folder / (digest(label) + ".result.json")
            counts["call_references"] += 1
            return (
                read(result) if result.exists() else {"error": "ambiguous_launch", "content": None}
            )
        raise ValueError("missing call " + label)

    def audit_replay(record, payload, ids, refs):
        response = call(record["call_label"], payload)
        try:
            if response["error"]:
                raise ValueError(response["error"])
            parsed = parse_fact_audit(response["content"], ids, refs)
            error = None
        except ValueError:
            parsed = {"facts": []}
            error = response["error"] or "invalid_judgment"
        require(
            record["facts"] == parsed["facts"] and record["error"] == error,
            "judgment replay differs",
        )
        require(record["request_sha256"] == digest(payload), "judgment request changed")

    for doc in sources:
        inv = read(root / "inventories" / (doc["id"] + ".json"))
        payload = reasoned(reference_request(doc))
        response = call(inv["call_label"], payload)
        try:
            if response["error"]:
                raise ValueError(response["error"])
            refs = parse_references(response["content"], doc)
            error = None
        except ValueError:
            refs, error = [], response["error"] or "invalid_inventory"
        require(inv["references"] == refs and inv["error"] == error, "inventory replay differs")
        check = read(root / "inventory-checks" / (doc["id"] + ".json"))
        if error:
            require(
                check == {"facts": [], "error": "inventory_unresolved"},
                "invalid inventory silently replaced",
            )
        else:
            audit_replay(check, inventory_check_request(doc, refs), {r["id"] for r in refs}, set())
        recovered = root / "inventory-checks-v2" / (doc["id"] + ".json")
        if recovered.exists():
            require(
                read(recovered) == derive_inventory_recovery(root, doc),
                "inventory recovery changed",
            )
            counts["inventory_recovery_records"] += 1
        counts["inventories"] += 1

    for model in models(root):
        direct = {
            d["id"]: read(root / "construction" / model / "direct" / (d["id"] + ".json"))["facts"]
            for d in sources
        }
        for arm in ARMS:
            folder = root / "construction" / model / arm
            require(
                {p.stem for p in folder.glob("*.json")} == source_ids,
                "incomplete construction " + model + "/" + arm,
            )
            for doc in sources:
                record = read(folder / (doc["id"] + ".json"))
                payload = construction_request(
                    model,
                    doc,
                    arm,
                    direct[doc["id"]] if arm != "direct" else [],
                    related[doc["id"]],
                    graph_hints(doc, related, direct) if arm != "direct" else [],
                )
                if model in SCHEMA_MODELS:
                    payload = schema_payload(
                        model,
                        doc,
                        arm,
                        direct[doc["id"]] if arm != "direct" else [],
                        related[doc["id"]],
                        graph_hints(doc, related, direct) if arm != "direct" else [],
                    )
                elif model in CLOUDFLARE_MODELS:
                    payload = wire_request(payload)
                require(record["request_sha256"] == digest(payload), "constructor request changed")
                require(
                    (record["model"], record["arm"], record["document"]) == (model, arm, doc["id"]),
                    "constructor namespace mismatch",
                )
                if model.startswith("local-"):
                    call_path = (
                        root / "native" / model / "calls" / (digest(payload) + ".result.json")
                    )
                    require(record["call_label"] == str(call_path), "native call path changed")
                    launch = read(call_path.with_name(digest(payload) + ".launch.json"))
                    require(launch["payload"] == payload, "native payload changed")
                    response = (
                        read(call_path)
                        if call_path.exists()
                        else {
                            "error": "ambiguous_launch",
                            "content": None,
                        }
                    )
                    counts["native_call_references"] += 1
                else:
                    response = call(record["call_label"], payload)
                replay = fact_record(response, doc)
                require(
                    all(record[k] == v for k, v in replay.items()), "construction replay differs"
                )
                counts["construction_records"] += 1
        for dirname in ("fact-audit", "fact-audit-secondary"):
            secondary = dirname.endswith("secondary")
            frozen = read(root / (dirname + "-frozen.json"))
            require(
                frozen["script_sha256"]
                == hashlib.sha256(Path("eval/kg_fact_audit.py").read_bytes()).hexdigest(),
                "audit code changed",
            )
            wanted = set(read(root / "frozen.json")["native_audit_sources"])
            for doc in sources:
                if secondary and doc["id"] not in wanted:
                    continue
                facts, mapping = candidates(root, doc, model)
                batches = [facts[i : i + 12] for i in range(0, len(facts), 12)]
                idx = read(root / dirname / model / (doc["id"] + ".index.json"))
                require(
                    idx
                    == {
                        "facts": facts,
                        "mapping": mapping,
                        "batches": [[f["id"] for f in b] for b in batches],
                    },
                    "audit mapping changed",
                )
                refs = read(root / "inventories" / (doc["id"] + ".json"))["references"]
                for i, batch in enumerate(batches):
                    payload = audit_request(doc, batch, refs)
                    if secondary:
                        payload.update(model="qwen3.8-27b", enable_thinking=False, max_tokens=8192)
                        payload.pop("thinking_budget", None)
                        payload["response_format"] = {"type": "json_object"}
                    record = read(root / dirname / model / (doc["id"] + "." + str(i) + ".json"))
                    audit_replay(record, payload, {f["id"] for f in batch}, {r["id"] for r in refs})
                    counts[dirname + "_batches"] += 1
                counts[dirname + "_source_indexes"] += 1

    frozen = read(root / "retrieval-frozen.json")
    require(frozen["code"] == hashes(), "retrieval code changed")
    old = {n: read(PREVIOUS / (n + ".json")) for n in ("corpus", "cases", "ranks", "initial")}
    require(frozen["inputs"] == {n: digest(v) for n, v in old.items()}, "retrieval input changed")
    docs = {d["id"]: d for d in old["corpus"]}
    cases = {c["id"]: c for c in old["cases"]}
    unique = set()
    for arm, (model, _hybrid) in RETRIEVAL_ARMS.items():
        packets = read(root / "packets" / (arm + ".json"))
        require(
            read(root / "packets" / (arm + ".manifest.json"))["sha256"] == digest(packets),
            "packet checksum changed",
        )
        require(len(packets) == 1000 and {p["id"] for p in packets} == set(cases), "incomplete QA")
        fact_ids = initial_fact_keys(old["initial"][model])
        for packet in packets:
            verify_packet(packet, docs, fact_ids)
            for key in ("question", "answers", "answerable", "dataset", "difficulty"):
                require(packet[key] == cases[packet["id"]][key], "question metadata changed")
            ident = evaluation_id(packet)
            result = read(root / "retrieval-results" / (ident + ".json"))
            require(
                result["id"] == ident and result["judge"] == MODEL, "assessment identity changed"
            )
            counts["question_arm_packets"] += 1
            if ident in unique:
                continue
            unique.add(ident)
            if result.get("reused_evaluation"):
                prior = read(PREVIOUS / "reasoned" / (ident + ".json"))
                require(
                    {k: v for k, v in result.items() if k != "reused_evaluation"} == prior,
                    "reused assessment changed",
                )
                counts["reused_complete_evaluations"] += 1
            ids = {s["id"] for s in packet["evidence"]["sources"]}
            response = call(reader_call_label(result, ident), reader_request(packet))
            require(
                result["reader"] == parse_reader(response["content"], ids), "reader replay differs"
            )
            for stage in ("blind", "semantic"):
                payload = reasoned(
                    blind_request(packet, result["reader"])
                    if stage == "blind"
                    else semantic_request(packet, result["reader"], result["blind"])
                )
                response = call(result["calls"][stage], payload)
                try:
                    if response["error"]:
                        raise ValueError(response["error"])
                    value = (
                        parse_blind(response["content"], ids, bool(packet["evidence"]["paths"]))
                        if stage == "blind"
                        else parse_semantic(response["content"])
                    )
                    error = None
                except ValueError:
                    value = None
                    error = response["error"] or "invalid_judgment"
                require(
                    result[stage] == value and result["errors"].get(stage) == error,
                    "retrieval judgment replay differs",
                )
    counts["unique_evaluations"] = len(unique)
    return {
        "passed": True,
        "counts": dict(counts),
        "ledger": {
            str(p): verify_ledger(p) for p in directories if not p.name.startswith("cloudflare-")
        },
        "cloudflare_ledger": (
            verify_cloudflare_ledger(root)
            if (root / "cloudflare-supplement-frozen.json").exists()
            else None
        ),
        "probe_attempts_separate": {
            folder: len(list((root / folder).glob("*.json")))
            for folder in ("probes", "probes-api-only", "probes-native-api")
        },
        "note": "Canceled local inference excluded; only completed API cohorts are compared.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    result = verify_all(args.root)
    (args.root / "verification.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
