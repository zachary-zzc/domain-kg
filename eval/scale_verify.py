"""Read-only replay of full experiment identities, provenance and assessment inputs."""

import argparse
import json
from collections import Counter
from pathlib import Path

from eval.scale_construction import graph_hints
from eval.scale_reasoned import MODEL, PRICE, manifest, reasoned
from eval.scale_retrieval import (
    MAX_CHARS,
    blind_request,
    code_hashes,
    context_size,
    evaluation_id,
    reader_request,
    semantic_request,
)
from eval.scale_single import ROOT, single_request, verify
from eval.scale_transport import PRICES, RETRY
from eval.source_role_eval import digest
from eval.source_role_expand import validated_facts
from eval.source_role_run import read


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def fact_key(document, fact):
    return digest({"d": document, **{k: fact[k] for k in ("s", "p", "o", "n", "q", "qualifiers")}})


def verify_packet(packet, docs, facts, *, max_chars=MAX_CHARS):
    evidence = packet["evidence"]
    sources = {s["id"]: s["text"] for s in evidence["sources"]}
    require(len(sources) == len(evidence["sources"]) <= 12, "duplicate/excess sources")
    require(context_size(evidence) <= max_chars, "evidence budget exceeded")
    for ident, text in sources.items():
        require(ident in docs, "unknown source")
        require(text == docs[ident]["title"] + "\n" + docs[ident]["text"], "source text changed")
    for path in evidence["paths"]:
        require(bool(path), "empty path")
        datasets = set()
        for step in path:
            ident = step["document"]
            require(ident in sources, "path source not retrieved")
            datasets.add(docs[ident].get("dataset"))
            value = dict(
                zip(
                    ("s", "p", "o", "n", "q"),
                    (step[k] for k in ("subject", "predicate", "object", "polarity", "quote")),
                    strict=True,
                )
            )
            value["qualifiers"] = step["qualifiers"]
            require(fact_key(ident, value) in facts, "path fact not in this model's graph")
        require(len(datasets) == 1, "cross-dataset graph path")


def verify_ledger(directory):
    counts = {"attempts": 0, "unknown_cost_attempts": 0, "ambiguous_launches": 0, "known_rmb": 0.0}
    prices = {**PRICES, MODEL: PRICE}
    launches = list(directory.glob("*.launch.json"))
    expected_results = {p.name.replace(".launch.", ".result.") for p in launches}
    require({p.name for p in directory.glob("*.result.json")} <= expected_results, "orphan result")
    for path in launches:
        row = read(path)
        label, payload = row["label"], row["payload"]
        require(path.name == digest(label) + ".launch.json", "ledger label mismatch")
        require(row["request_sha256"] == digest(payload), "ledger payload changed")
        require(payload["model"] in prices, "unknown ledger model")
        base, attempt = label.rsplit(":", 1)
        require(attempt in {"0", "1", "2"}, "retry allowance exceeded")
        if attempt != "0":
            previous = read(
                directory / (digest(base + ":" + str(int(attempt) - 1)) + ".result.json")
            )
            require(previous["error"] in RETRY, "semantic/ambiguous retry not permitted")
            require(previous["request_sha256"] == digest(payload), "retry changed payload")
        counts["attempts"] += 1
        result_path = path.with_name(path.name.replace(".launch.", ".result."))
        if not result_path.exists():
            counts["ambiguous_launches"] += 1
            counts["unknown_cost_attempts"] += 1
            continue
        result = read(result_path)
        require(result["label"] == label, "ledger result label mismatch")
        require(result["request_sha256"] == digest(payload), "ledger result hash mismatch")
        require(result["model"] == payload["model"], "ledger result model mismatch")
        require(result["usage"] == (result.get("response") or {}).get("usage"), "usage changed")
        usage, price = result["usage"] or {}, prices[payload["model"]]
        tokens = [usage.get(k) for k in ("prompt_tokens", "completion_tokens")]
        known = price is not None and all(type(n) is int and 0 <= n <= 1_000_000 for n in tokens)
        if known:
            expected = sum(n * p for n, p in zip(tokens, price, strict=True)) / 1_000_000
            require(
                result["actual_rmb"] is not None and abs(result["actual_rmb"] - expected) < 1e-10,
                "ledger cost changed",
            )
            counts["known_rmb"] += expected
        else:
            require(result["actual_rmb"] is None, "unknown usage/price must not be treated as free")
            counts["unknown_cost_attempts"] += 1
    return counts


def verify_all(root):
    verify(root)
    require(read(root / "retrieval-frozen.json")["code"] == code_hashes(), "retrieval code changed")
    require((root / "reasoned-frozen.json").exists(), "missing reasoned manifest")
    manifest(root)
    docs = {d["id"]: d for d in read(root / "corpus.json")}
    cases = {c["id"]: c for c in read(root / "cases.json")}
    require(len(docs) == 6566 and len(cases) == 1000, "incomplete input corpus")
    initial, related = (read(root / (n + ".json")) for n in ("initial", "related"))
    qualified = read(root / "qualification.json")
    arms = ["RAG", *(m for m, q in qualified.items() if q["eligible"])]
    audit_ids = set(read(root / "construction-frozen.json")["audit_case_ids"])
    counts = Counter()
    facts_by_arm = {"RAG": set()}

    def check_call(directory, label, payload):
        stem = digest(label)
        launch = read(root / directory / (stem + ".launch.json"))
        result_path = root / directory / (stem + ".result.json")
        require(launch["label"] == label, "call label mismatch")
        require(launch["request_sha256"] == digest(payload), "call request identity mismatch")
        require(digest(launch["payload"]) == digest(payload), "stored payload changed")
        row = read(result_path) if result_path.exists() else {"error": "ambiguous_launch"}
        if row["error"] != "ambiguous_launch":
            require(row["request_sha256"] == digest(payload), "call result identity mismatch")
        counts["call_references_verified"] += 1
        return row

    for model in arms[1:]:
        folder = root / "construction" / model
        require(
            {p.stem for p in folder.glob("*.json")} == set(docs), "incomplete source construction"
        )
        facts = set()
        for ident, doc in docs.items():
            row = read(folder / (ident + ".json"))
            require(row["model"] == model and row["document"] == ident, "construction namespace")
            payload = single_request(
                model,
                doc,
                initial[model].get(ident, []),
                related[ident],
                graph_hints(doc, related, initial[model]),
            )
            require(digest(payload) == row["request_sha256"], "construction request changed")
            call = check_call("calls", row["call_label"], payload)
            if row["error"] is None:
                replay = validated_facts(call["content"], doc, 8)
                require(
                    all(row[k] == v for k, v in replay.items()), "source provenance replay failed"
                )
            else:
                require(not row["facts"], "failed construction must not insert fallback facts")
            facts.update(fact_key(ident, f) for f in row["facts"])
            counts["construction_sources_verified"] += 1
        facts_by_arm[model] = facts

    unique = {name: set() for name in ("evaluations", "reasoned", "audit")}
    for arm in arms:
        packets = read(root / "packets" / (arm + ".json"))
        frozen = read(root / "packets" / (arm + ".manifest.json"))
        require(digest(packets) == frozen["sha256"], "packet checksum changed")
        require(
            len(packets) == len(cases) and {p["id"] for p in packets} == set(cases), "incomplete QA"
        )
        if arm == "RAG":
            require(frozen["facts"] == 0 and not frozen["graph_inputs"], "RAG depends on graph")
        for file, expected in frozen["graph_inputs"].items():
            require(digest(read(Path(file))) == expected, "graph input changed after retrieval")
        for packet in packets:
            verify_packet(packet, docs, facts_by_arm[arm])
            for key in ("question", "answers", "answerable", "dataset", "difficulty"):
                require(packet[key] == cases[packet["id"]][key], "question metadata changed")
            require(arm != "RAG" or not packet["evidence"]["paths"], "graph paths in RAG")
            ident = evaluation_id(packet)
            original = read(root / "evaluations" / (ident + ".json"))
            for directory in unique:
                if directory == "audit" and packet["id"] not in audit_ids:
                    continue
                result = read(root / directory / (ident + ".json"))
                require(result["id"] == ident, "assessment packet identity mismatch")
                require(result["reader"] == original["reader"], "reader changed between judges")
                if ident in unique[directory]:
                    continue
                unique[directory].add(ident)
                calls = result["calls"]
                if directory == "reasoned":
                    folder, convert = "reasoned-calls", reasoned
                else:
                    folder, convert = "evaluation-calls", lambda value: value
                    check_call(folder, calls["reader"], reader_request(packet))
                check_call(
                    folder,
                    calls["blind"],
                    convert(blind_request(packet, result["reader"], result["judge"])),
                )
                check_call(
                    folder,
                    calls["semantic"],
                    convert(
                        semantic_request(packet, result["reader"], result["blind"], result["judge"])
                    ),
                )
            counts["question_arm_packets_verified"] += 1
    counts.update({k + "_unique_verified": len(v) for k, v in unique.items()})
    directories = [
        Path(".cache/full-scale-protocol-2026-10-21-v1/calls"),
        Path(".cache/full-scale-retrieval-2026-10-21-v1/calls"),
        *(root / d for d in ("calls", "evaluation-calls", "reasoned-calls")),
    ]
    ledger = {str(d): verify_ledger(d) for d in directories}
    return {
        "passed": True,
        "counts": dict(counts),
        "arms": arms,
        "max_content_chars": MAX_CHARS,
        "ledger": ledger,
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
