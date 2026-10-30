"""Reproducible offline evidence comparison; never calls a model or scores answer EM."""

import argparse
import asyncio
import hashlib
import json
import re
import socket
from collections import defaultdict
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from domain_kg.retrieval.fusion import fuse, fuse_evidence, select_evidence
from domain_kg.retrieval.ranking import BM25
from eval.scoped_retrieval import OfflineIndex, constrain_graph, evidence_coverage, path_context


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def implementation_hashes():
    paths = sorted(Path("src/domain_kg/retrieval").glob("*.py")) + [
        Path("src/domain_kg/graph/repository.py"),
        Path("eval/scoped_retrieval.py"),
        Path("eval/scoped_retrieval_run.py"),
    ]
    return {str(path): digest(path) for path in paths}


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def load_saved(root):
    """Validate retries and retain only saved construction and embedding artifacts."""
    groups, hashes = defaultdict(list), {}
    for path in sorted((root / "calls").glob("*.json")):
        hashes[path.name] = digest(path)
        row = json.loads(path.read_text())
        if row["kind"] != "formal_graph" and not (
            row["kind"] == "embedding"
            and row["label"].startswith(("formal-docs:", "formal-queries:"))
        ):
            continue
        label = re.sub(r"#transport-[12]$", "", row["label"])
        groups[row["kind"], row["model"], label].append(row)
    chosen = []
    for key, rows in sorted(groups.items()):
        if len({r["request_sha256"] for r in rows}) != 1:
            raise ValueError("saved retry changed request")
        successes = [r for r in rows if r["error"] is None]
        if len(successes) > 1:
            raise ValueError("multiple successful saved calls")
        row = successes[0] if successes else sorted(rows, key=lambda r: r["label"])[-1]
        chosen.append(
            {k: row[k] for k in ("kind", "model", "graph", "vectors") if k in row}
            | {"label": key[2], "error": row["error"]}
        )
    return chosen, hashes


def saved_vector_ranks(rows, docs, cases, original):
    import numpy as np

    arrays = []
    for prefix, count in (("formal-docs:", len(docs)), ("formal-queries:", len(cases))):
        parts = sorted(
            (r for r in rows if r["kind"] == "embedding" and r["label"].startswith(prefix)),
            key=lambda r: int(r["label"].split(":")[1]),
        )
        if [int(r["label"].split(":")[1]) for r in parts] != list(range(0, count, 10)):
            raise ValueError("incomplete saved embedding sequence")
        if any(r["error"] or "vectors" not in r for r in parts):
            raise ValueError("embedding missing; no network fallback")
        matrix = np.concatenate([np.asarray(r["vectors"], dtype="float32") for r in parts])
        if matrix.shape != (count, 1024) or not np.isfinite(matrix).all():
            raise ValueError("embedding dimensions or values differ")
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        if (norms == 0).any():
            raise ValueError("zero embedding")
        arrays.append(matrix / norms)
    scores = arrays[1] @ arrays[0].T
    ranks = [[docs[int(i)]["id"] for i in np.argsort(-row, kind="stable")[:50]] for row in scores]
    if [rank[:12] for rank in ranks] != original:
        raise ValueError("saved vector ranking was not reproduced")
    return ranks


def summarize(records):
    values = [r for r in records if r["coverage"]["complete"] is not None]
    return {
        "questions": len(records),
        "reference_answerable": len(values),
        "reference_complete_count": sum(r["coverage"]["complete"] for r in values),
        "reference_complete": sum(r["coverage"]["complete"] for r in values) / len(values)
        if values
        else None,
        "reference_recall": sum(r["coverage"]["recall"] for r in values) / len(values)
        if values
        else None,
        "mean_passages": sum(len(r["documents"]) for r in records) / max(1, len(records)),
        "mean_source_characters": sum(r["coverage"]["source_characters"] for r in records)
        / max(1, len(records)),
        "mean_path_characters": sum(r.get("path_characters", 0) for r in records)
        / max(1, len(records)),
    }


async def run(root, output):
    if output.exists():
        raise ValueError("choose a new output directory; prior results are immutable")
    output.mkdir(parents=True, mode=0o700)
    code_hashes = implementation_hashes()
    frozen = json.loads((root / "formal-frozen.json").read_text())
    for name, expected in frozen.items():
        if digest(Path(name)) != expected:
            raise ValueError("frozen input changed: " + name)
    docs = json.loads((root / "corpus.json").read_text())
    cases = json.loads((root / "cases.json").read_text())
    review = json.loads((root / "full-semantic-review/summary.json").read_text())
    primary = set(review["primary_question_ids"])
    attempted = set(review["common_question_ids"])
    pairs = json.loads((root / "full-semantic-review/pairs.json").read_text())
    attempted.update(p["case_id"] for p in pairs)
    models = list(review["models"])
    rows, call_hashes = load_saved(root)
    ranks = saved_vector_ranks(
        rows, docs, cases, json.loads((root / "vector-ranks.json").read_text())
    )
    save(output / "vector-ranks-50.json", ranks)
    bm25 = BM25({d["id"]: d["title"] + "\n" + d["text"] for d in docs})
    summary, records, packets = {}, [], []
    baseline_documents = {}
    # Fixed before outcomes: three reviewed questions per dataset, ordered by a hash.
    sample = set()
    for dataset in sorted({c["dataset"] for c in cases}):
        eligible = [c for c in cases if c["id"] in primary and c["dataset"] == dataset]
        sample.update(
            c["id"]
            for c in sorted(eligible, key=lambda c: hashlib.sha256(c["id"].encode()).hexdigest())[
                :3
            ]
        )
    for model in models:
        facts = [
            f
            for row in rows
            if row["kind"] == "formal_graph" and row["model"] == model
            for f in (row.get("graph") or {}).get("facts", [])
        ]
        index = OfflineIndex(
            docs, facts, kg_id=uuid5(NAMESPACE_URL, "offline/public-qa-frozen"), bm25=bm25
        )
        model_records = []
        for position, (case, vector_ids) in enumerate(zip(cases, ranks, strict=True)):
            cohort = (
                "reviewed"
                if case["id"] in primary
                else "unanswered"
                if case["id"] not in attempted
                else "flagged_or_partial"
            )
            branches, view = await index.retrieve(case["question"], vector_ids)
            strong, strong_cut = select_evidence(
                fuse({k: branches[k] for k in ("vector", "lexical")}),
                top_k=12,
                max_chars=12000,
            )
            guided, guided_cut = select_evidence(
                fuse_evidence(branches), top_k=12, max_chars=12000
            )
            constrained = constrain_graph(case["question"], branches["graph"], view)
            relation, relation_cut = select_evidence(
                fuse_evidence({**branches, "graph": constrained}), top_k=12, max_chars=12000
            )
            v12, v12_cut = select_evidence(branches["vector"][:12], top_k=12, max_chars=12000)
            context = path_context(index, relation, view)
            arms = {
                "V12": (v12, v12_cut),
                "RAG": (strong, strong_cut),
                "GUIDED": (guided, guided_cut),
                "REL": (relation, relation_cut),
                "PATH": (relation, relation_cut),
            }
            for arm, (selected, truncated) in arms.items():
                record = {
                    "model": model,
                    "id": case["id"],
                    "dataset": case["dataset"],
                    "cohort": cohort,
                    "arm": arm,
                    "documents": [index.document_id(x) for x in selected],
                    "coverage": evidence_coverage(case, selected, index),
                    "truncated": truncated,
                    "graph_truncated": view.truncation
                    if arm in ("GUIDED", "REL", "PATH")
                    else False,
                    "constrained_candidates": len(constrained) if arm in ("REL", "PATH") else 0,
                    "path_count": len(context["paths"]) if arm == "PATH" else 0,
                    "path_characters": context["path_characters"] if arm == "PATH" else 0,
                }
                model_records.append(record)
                if arm in ("RAG", "V12"):
                    key = (case["id"], arm)
                    expected = baseline_documents.setdefault(key, record["documents"])
                    if expected != record["documents"]:
                        raise ValueError("non-graph baseline changed with construction model")
            if case["id"] in sample and model == models[3]:
                packets.append(
                    {
                        "id": case["id"],
                        "dataset": case["dataset"],
                        "question": case["question"],
                        "arms": {
                            arm: [
                                {"document": index.document_id(x), "text": x.text} for x in selected
                            ]
                            for arm, (selected, _) in arms.items()
                            if arm in ("RAG", "REL")
                        },
                        "paths": context,
                    }
                )
            if position % 200 == 0:
                print(f"{model}: {position}/{len(cases)}", flush=True)
        summary[model] = {
            cohort: {
                arm: summarize(
                    [r for r in model_records if r["cohort"] == cohort and r["arm"] == arm]
                )
                for arm in arms
            }
            for cohort in ("reviewed", "unanswered", "flagged_or_partial")
        }
        records.extend(model_records)
        print(f"{model}: complete", flush=True)
    current_hashes = {p.name: digest(p) for p in sorted((root / "calls").glob("*.json"))}
    if call_hashes != current_hashes:
        raise ValueError("saved calls changed during offline evaluation")
    if code_hashes != implementation_hashes():
        raise ValueError("implementation changed during replay")
    save(output / "records.json", records)
    save(output / "semantic-packets.json", packets)
    inputs = {
        str(root / name): digest(root / name)
        for name in (
            "corpus.json",
            "cases.json",
            "vector-ranks.json",
            "full-semantic-review/summary.json",
            "full-semantic-review/pairs.json",
        )
    }
    save(
        output / "summary.json",
        {
            "models": summary,
            "new_provider_calls": 0,
            "new_answer_accuracy_measured": False,
            "vector_top12_reproduced": len(cases),
            "saved_call_count": len(call_hashes),
            "input_sha256": inputs,
            "code_sha256": code_hashes,
            "saved_calls_sha256": hashlib.sha256(
                json.dumps(call_hashes, sort_keys=True).encode()
            ).hexdigest(),
            "semantic_sample_ids": sorted(sample),
            "shared_production_code": [
                "GraphRepository.traverse",
                "rank_seeds",
                "trace_paths",
                "rank_graph_candidates",
                "BM25",
                "fuse",
                "fuse_evidence",
                "select_evidence",
            ],
            "limits": {
                "candidate_pool_per_branch": 50,
                "max_hops": 2,
                "max_nodes": 200,
                "max_edges": 400,
                "top_k": 12,
                "source_text_characters": 12000,
                "additional_path_characters": 2000,
            },
            "limitations": [
                "Reference support coverage is diagnostic, not semantic correctness or answer EM.",
                "Saved graphs use dataset-scoped name hints, not verified production identity.",
                "REL constrains lexical anchors/terminal predicates offline; no query LLM.",
                "GUIDED uses production soft ranking; REL's query planner is experimental.",
                "PATH and REL have identical selected evidence; path metadata is additional.",
                "No new extraction or answers; graph correctness and path benefit unproven.",
                "Public I/O adapter replaces stores/ACL; real-store security tests are separate.",
            ],
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(".cache/dual-path-2026-10-20-v3a"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    def deny_network(*args, **kwargs):
        raise RuntimeError("offline evaluator prohibits network access")

    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    asyncio.run(run(args.root, args.output))


if __name__ == "__main__":
    main()
