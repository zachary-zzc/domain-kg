"""Offline full semantic review inventory and independent citation scoring.

No network, local model, fuzzy similarity, or inference judge. Native labels
are required for substantive answers; deterministic grouping only saves repeats.
"""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from eval.dual_path_data import json_object
from eval.dual_path_retrieval import source_units
from eval.dual_path_run import load_rows, verify
from eval.dual_path_transport import logical_rows
from eval.public_qa_data import fingerprint
from eval.public_qa_prepare import file_hash, save

ROOT = Path(".cache/dual-path-2026-10-20-v3a")


def group_key(question_id, answer):
    return fingerprint([question_id, answer])


def content_fields(raw):
    obj = json_object(raw)
    if isinstance(obj, dict) and isinstance(obj.get("answer"), (str, list)):
        return {"kind": "explicit", "answer": obj["answer"], "abstain": obj.get("abstain")}
    return {"kind": "raw", "answer": raw, "abstain": None}


def citation_score(case, sources, raw):
    arrays = []
    decoder = json.JSONDecoder()
    for match in re.finditer(r'"citations"\s*:\s*', raw):
        try:
            value, _ = decoder.raw_decode(raw[match.end() :])
            if isinstance(value, list) and all(isinstance(x, str) for x in value):
                arrays.append(value)
        except ValueError:
            pass
    zero = {
        "complete": 0.0,
        "recall": 0.0,
        "precision": 0.0,
        "cited": [],
        "invalid_ids": [],
        "status": "missing",
        "recovery": None,
    }
    if not arrays:
        return zero
    if len({frozenset(a) for a in arrays}) != 1:
        return {**zero, "status": "ambiguous"}
    wire = {f"e{i}": source["id"] for i, source in enumerate(sources)}
    refs = set(arrays[0])
    cited = {wire[r] for r in refs if r in wire}
    unknown = refs - set(wire)
    gold = set(case["support"])
    overlap = len(cited & gold)
    return {
        "complete": float(gold <= cited) if gold else None,
        "recall": overlap / len(gold) if gold else None,
        "precision": overlap / len(refs) if refs else 0.0,
        "cited": sorted(cited),
        "invalid_ids": sorted(unknown),
        "status": "invalid_ids" if unknown else "ok",
        "recovery": "json" if json_object(raw) is not None else "explicit_array",
    }


def raw_content(row):
    try:
        return row["raw"]["choices"][0]["message"].get("content", "") or ""
    except (KeyError, TypeError, IndexError):
        return ""


def prepare(root):
    verify(root, "formal-frozen.json")
    out = root / "full-semantic-review"
    out.mkdir(mode=0o700, exist_ok=False)
    cases = {c["id"]: c for c in json.loads((root / "cases.json").read_text())}
    docs = {d["id"]: d for d in json.loads((root / "corpus.json").read_text())}
    retrieval = {
        (r["model"], r["id"], r["arm"]): r
        for r in json.loads((root / "formal-retrieval.json").read_text())
    }
    rows = [r for r in logical_rows(load_rows(root / "calls")) if r["kind"] == "formal_answer"]
    old_packets = json.loads((root / "semantic-review/packets.json").read_text())
    old_labels = {
        (label["case_index"], label["group_index"]): label
        for label in json.loads((root / "semantic-review/judgments.json").read_text())
    }
    reusable = {}
    for c in old_packets:
        for i, g in enumerate(c["groups"]):
            answer = g["prediction"] if "prediction" in g else g["raw"]
            key = group_key(c["id"], answer)
            label = old_labels[c["index"], i]
            if key in reusable and reusable[key]["judgment"] != label["judgment"]:
                raise ValueError("prior judgments conflict after content-only grouping")
            reusable[key] = label
    grouped, pairs = {}, []
    for row in rows:
        ident, arm = row["label"].rsplit("|", 1)
        case = cases[ident]
        text = raw_content(row)
        fields = content_fields(text)
        key = group_key(ident, fields["answer"])
        g = grouped.setdefault(
            key,
            {
                "key": key,
                "case_id": ident,
                "kind": fields["kind"],
                "answer": fields["answer"],
                "members": [],
            },
        )
        g["members"].append(row["identity"])
        sources = source_units(retrieval[row["model"], ident, arm]["documents"], docs)
        pairs.append(
            {
                "identity": row["identity"],
                "case_id": ident,
                "model": row["model"],
                "arm": arm,
                "group": key,
                "original_error": row["error"],
                "old_scores": row.get("scores"),
                "answer_sha256": fingerprint(text),
                "citation": citation_score(case, sources, text),
            }
        )
    ordered_cases = sorted({r["case_id"] for r in pairs})
    packets, labels = [], {}
    for index, ident in enumerate(ordered_cases):
        case = cases[ident]
        groups = sorted(
            [g for g in grouped.values() if g["case_id"] == ident], key=lambda g: g["key"]
        )
        for i, group in enumerate(groups):
            group["index"] = i
            if group["key"] in reusable:
                old = reusable[group["key"]]
                labels[group["key"]] = {
                    "judgment": old["judgment"],
                    "reason": old["reason"],
                    "origin": "prior_native_review",
                }
            elif group["answer"] in ("", []):
                labels[group["key"]] = {
                    "judgment": "a",
                    "reason": (
                        "No substantive answer text returned; content is empty "
                        "independently of citation/protocol."
                    ),
                    "origin": "empty_content",
                }
        packets.append({"index": index, **case, "groups": groups})
    save(out / "packets.json", packets)
    save(out / "pairs.json", pairs)
    save(out / "labels.json", labels)
    save(out / "question-quality.json", {})
    manifest = {
        "responses": len(pairs),
        "questions": len(packets),
        "distinct_answers": len(grouped),
        "preclassified_groups": len(labels),
        "preclassified_origins": dict(Counter(label["origin"] for label in labels.values())),
        "original_call_files": len(list((root / "calls").glob("*.json"))),
        "original_files": {
            n: file_hash(root / n)
            for n in (
                "cases.json",
                "corpus.json",
                "formal-retrieval.json",
                "verified-analysis.json",
                "formal-frozen.json",
            )
        },
        "previous_review": {
            n: file_hash(root / "semantic-review" / n)
            for n in ("packets.json", "judgments.json", "adjudication.json")
        },
        "semantics": (
            "Native content review; no similarity proxy, no model inference; "
            "reference eligibility shared across arms/models."
        ),
        "citations": (
            "Explicit returned citations array, independently decoded, exact wire IDs remapped; "
            "conflicting duplicate arrays flagged, no automatic union."
        ),
        "new_cloud_inference_calls": 0,
    }
    save(out / "manifest.json", manifest)
    print(json.dumps(manifest), flush=True)


def show(root, start, stop):
    out = root / "full-semantic-review"
    packets = json.loads((out / "packets.json").read_text())
    labels = json.loads((out / "labels.json").read_text())
    for c in packets[start:stop]:
        groups = []
        for g in c["groups"]:
            if g["key"] in labels:
                continue
            text = g["answer"]
            groups.append({"g": g["index"], "kind": g["kind"], "a": text, "n": len(g["members"])})
        print(
            json.dumps(
                {
                    "i": c["index"],
                    "id": c["id"],
                    "dataset": c["dataset"],
                    "q": c["question"],
                    "gold": c["answers"],
                    "answerable": c["answerable"],
                    "support_titles": sorted(
                        {u["title"] for u in c["contexts"] if u["id"] in c["support"]}
                    ),
                    "groups": groups,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("prepare", "show"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--stop", type=int, default=10)
    args = parser.parse_args()
    if args.phase == "prepare":
        prepare(args.root)
    else:
        show(args.root, args.start, args.stop)
