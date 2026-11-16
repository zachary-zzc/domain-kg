"""Frozen KG contribution experiment. All commands are offline except explicit API phases."""

import argparse
import asyncio
import hashlib
import json
import time
from collections import Counter
from pathlib import Path

import numpy as np

from eval import kg_quality as q
from eval.kg_quality_api import WORK_MODELS, CloudCalls, catalog, cloud_client, cloud_key, save_once
from eval.kg_quality_build import CONSTRUCTION_SEMANTICS, construct
from eval.kg_quality_graph import build_index, graph_statistics, load_graph
from eval.scale_guard import pool
from eval.scale_retrieval import context_size, pack
from eval.scoped_retrieval_run import implementation_hashes
from eval.source_role_eval import digest
from eval.source_role_run import read, run_lock, write_new

CALIBRATION = Path(__file__).with_name("fixtures") / "kg_quality_calibration.json"


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code_hashes():
    paths = [
        Path("pyproject.toml"),
        Path("uv.lock"),
        *[
            Path("eval") / name
            for name in (
                "kg_quality.py",
                "kg_quality_graph.py",
                "kg_quality_run.py",
                "kg_quality_api.py",
                "kg_quality_build.py",
                "kg_quality_bbio.py",
                "kg_quality_merge.py",
            )
        ],
        CALIBRATION,
        *sorted(Path("src/domain_kg/providers").glob("*.py")),
        *[
            Path("src/domain_kg") / name
            for name in (
                "ingestion/extraction.py",
                "ingestion/models.py",
                "ingestion/output_splitting.py",
                "ingestion/chunking.py",
                "ingestion/warnings.py",
                "graph/schema.py",
                "graph/models.py",
                "models.py",
            )
        ],
        *[
            Path("eval") / name
            for name in (
                "scale_retrieval.py",
                "source_role_evidence.py",
                "source_role_eval.py",
                "source_role_run.py",
                "scale_guard.py",
                "scale_transport.py",
                "public_qa_run.py",
                "dual_path_data.py",
            )
        ],
    ]
    return {**implementation_hashes(), **{str(p): file_hash(p) for p in paths}}


def seal(root, names, name="frozen.json"):
    save_once(
        root / name, {"files": {n: file_hash(root / n) for n in names}, "code": code_hashes()}
    )


def verify(root, name="frozen.json"):
    frozen = read(root / name)
    if frozen["code"] != code_hashes():
        raise ValueError("frozen evaluation implementation changed; use a new run directory")
    for path, expected in frozen["files"].items():
        if file_hash(root / path) != expected:
            raise ValueError(f"frozen input changed: {path}")


def draft(source, output):
    cases, docs = read(source / "cases.json"), read(source / "corpus.json")
    _, units = q.validate_corpus(docs)
    write_new(output / "reviews.json", q.review_template(cases))
    write_new(
        output / "question-review-packets.json",
        [
            {
                "case": c,
                "original_sha256": digest(c),
                "support_sources": [
                    d for d in docs if d["id"] in {units[u][0] for u in c["support"]}
                ],
                "checks": [
                    "Self-contained in shared corpus?",
                    "Correct premise and time/event scope?",
                    "Reference answer follows from source?",
                    "All necessary bridges annotated?",
                    "Edits introduce neither answer nor a hidden intermediate entity?",
                ],
            }
            for c in cases
        ],
    )


def selected_models(root):
    path = root / "protocol.json"
    models = tuple(read(path)["working_models"]) if path.exists() else WORK_MODELS
    if not models or len(set(models)) != len(models) or not set(models) <= set(WORK_MODELS):
        raise ValueError("invalid frozen model selection")
    return models


def selected_judge(root):
    path = root / "protocol.json"
    judge = read(path)["judge"] if path.exists() else q.JUDGE
    if judge not in WORK_MODELS:
        raise ValueError("invalid frozen judge selection")
    return judge


def frozen_bool(root, name):
    path = root / "protocol.json"
    value = read(path).get(name, False) if path.exists() else False
    if type(value) is not bool:
        raise ValueError("invalid frozen policy: " + name)
    return value


def uses_separate_grounding(root):
    return frozen_bool(root, "separate_grounding")


def assessment_options(root):
    return {
        "judge": selected_judge(root),
        "separate_grounding": uses_separate_grounding(root),
        "bounded_review": frozen_bool(root, "bounded_review"),
    }


def freeze(
    source,
    reviews,
    output,
    *,
    models=WORK_MODELS,
    construction_semantics="source_roles_v1",
    judge=q.JUDGE,
    separate_grounding=False,
    bounded_review=False,
    source_merge=False,
):
    if not models or len(set(models)) != len(models) or not set(models) <= set(WORK_MODELS):
        raise ValueError("invalid frozen model selection")
    if construction_semantics not in CONSTRUCTION_SEMANTICS:
        raise ValueError("invalid construction semantics")
    if judge not in WORK_MODELS:
        raise ValueError("invalid frozen judge selection")
    if type(separate_grounding) is not bool:
        raise ValueError("invalid frozen grounding policy")
    if type(bounded_review) is not bool or type(source_merge) is not bool:
        raise ValueError("invalid frozen merge/review policy")
    if bounded_review and not separate_grounding:
        raise ValueError("bounded review requires reference-isolated judging")
    cases, docs = read(source / "cases.json"), read(source / "corpus.json")
    selected = q.reviewed_cases(cases, docs, read(reviews))
    output.mkdir(parents=True, exist_ok=False)
    values = {
        "cases.json": selected,
        "corpus.json": docs,
        "windows.json": read(source / "windows.json"),
        "reviews.json": read(reviews),
        "protocol.json": {
            **q.PROTOCOL,
            "working_models": models,
            "construction_semantics": construction_semantics,
            "judge": judge,
            "separate_grounding": separate_grounding,
            "bounded_review": bounded_review,
            "source_merge": source_merge,
            "generative_retrieval": "shared_original_source_rerank" if source_merge else None,
            "source_pool_limit": 50,
            "source_merge_order": "validated ordered subset; recall-ranked backfill",
            "merge_context_tokens": 131072,
            "merge_failure_policy": "unavailable; quality unknown; no automatic retry",
        },
        "provenance.json": {
            "source": str(source.resolve()),
            "construction": "one new graph per working model; no cross-model reuse",
            "inputs": {
                str(p): file_hash(p)
                for p in (
                    source / "cases.json",
                    source / "corpus.json",
                    source / "windows.json",
                    reviews,
                )
            },
            "included": len(selected),
            "excluded": len(cases) - len(selected),
            "exclusion_reasons": [r for r in read(reviews) if r["decision"] == "exclude"],
            "corpus_documents": len(docs),
            "text_utf8_bytes": sum(len((d["title"] + "\n" + d["text"]).encode()) for d in docs),
            "question_datasets": dict(Counter(c["dataset"] for c in selected)),
            "split": "development: source pool previously inspected; not a holdout",
        },
    }
    for name, value in values.items():
        write_new(output / name, value)
    seal(output, list(values))


def import_ranks(root, original):
    """Only unchanged queries may reuse the historical, positional rank file."""
    cases, docs = read(root / "cases.json"), read(root / "corpus.json")
    profile = read(original / "definition.json")["profile"]
    if (profile["embedding_model"], profile["embedding_dimensions"]) != (
        q.PROTOCOL["embedding"],
        q.PROTOCOL["dimensions"],
    ):
        raise ValueError("cached ranking embedding profile changed")
    old_cases, old_docs = read(original / "cases.json"), read(original / "corpus.json")
    if docs != old_docs:
        raise ValueError("cached ranking corpus changed")
    old = {
        c["id"]: (c["question"], rank)
        for c, rank in zip(old_cases, read(original / "vector-ranks.json"), strict=True)
    }
    ranks = []
    for case in cases:
        if case["id"] not in old or old[case["id"]][0] != case["question"]:
            raise ValueError("query changed; regenerate embeddings")
        ranks.append(old[case["id"]][1])
    bundle = q.rank_bundle(cases, docs, ranks)
    bundle["provenance"] = {
        str(original / n): file_hash(original / n)
        for n in ("cases.json", "corpus.json", "vector-ranks.json", "definition.json")
    }
    save_once(root / "ranks.json", bundle)


async def embed(root, texts, client, key):
    matrices = []
    for start in range(0, len(texts), 10):
        payload = {
            "model": q.PROTOCOL["embedding"],
            "input": texts[start : start + 10],
            "dimensions": q.PROTOCOL["dimensions"],
            "encoding_format": "float",
        }
        row = await CloudCalls(root / "embedding-calls", key).raw(client, payload, kind="embedding")
        if row["error"]:
            raise ValueError("embedding provider failure: " + row["error"])
        entries = sorted(row["response"]["data"], key=lambda e: e["index"])
        matrix = np.asarray([e["embedding"] for e in entries], dtype="float32")
        if (
            [e["index"] for e in entries] != list(range(len(payload["input"])))
            or matrix.shape != (len(payload["input"]), q.PROTOCOL["dimensions"])
            or not np.isfinite(matrix).all()
        ):
            raise ValueError("invalid embedding shape/value")
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        if (norms == 0).any():
            raise ValueError("zero embedding")
        matrices.append(matrix / norms)
    return np.concatenate(matrices)


async def rank(root, key):
    cases, docs = read(root / "cases.json"), read(root / "corpus.json")
    async with cloud_client() as client:
        documents = await embed(root, [d["title"] + "\n" + d["text"] for d in docs], client, key)
        queries = await embed(root, [c["question"] for c in cases], client, key)
    ranks = [
        [docs[int(i)]["id"] for i in np.argsort(-row, kind="stable")[:50]]
        for row in queries @ documents.T
    ]
    save_once(root / "ranks.json", q.rank_bundle(cases, docs, ranks))


async def retrieve_packets(cases, docs, graph, bundle, *, model, merge=None):
    if model not in WORK_MODELS or graph.get("model") != model:
        raise ValueError("retrieval graph belongs to a different working model")
    ranks = q.validate_ranks(bundle, cases, docs)
    index = build_index(docs, graph)
    from domain_kg.models import Namespace

    namespace = Namespace(org_id=index.org_id, kg_id=index.kg_id)
    by_doc, units = q.validate_corpus(docs)
    packets = []
    for case in cases:
        start = time.perf_counter()
        branches = {
            "vector": [index.candidates[k] for k in ranks[case["id"]][:50]],
            "lexical": [],
        }
        dense_trace, graph_trace = {}, {}
        if merge:
            selected, dense_trace = await merge(
                case["question"], {"vector": branches["vector"]}, model, namespace=namespace
            )
            dense = {
                "sources": [{"id": index.document_id(s), "text": s.text} for s in selected],
                "paths": [],
            }
        else:
            dense, _ = pack(index, branches, None, graph=False)
        dense_seconds = time.perf_counter() - start
        start = time.perf_counter()
        branches, view = await index.retrieve(case["question"], ranks[case["id"]])
        if merge:
            selected, graph_trace = await merge(
                case["question"],
                {k: branches[k] for k in ("vector", "graph")},
                model,
                namespace=namespace,
            )
            with_paths = {
                "sources": [{"id": index.document_id(s), "text": s.text} for s in selected],
                "paths": [],
            }
        else:
            with_paths, _ = pack(index, branches, view, graph=True)
        graph_seconds = time.perf_counter() - start
        references = [
            {"id": u, "title": by_doc[units[u][0]]["title"], "text": units[u][1]}
            for u in case["support"]
        ]
        for arm, evidence in (("vector_rag", dense), ("domain_kg", with_paths)):
            packets.append(
                {
                    **case,
                    "arm": arm,
                    "model": model,
                    "evidence": evidence,
                    "retrieval_error": (dense_trace if arm == "vector_rag" else graph_trace).get(
                        "error"
                    ),
                    "reference_sources": references,
                    "diagnostics": {
                        "merge": dense_trace if arm == "vector_rag" else graph_trace,
                        "content_chars": context_size(evidence),
                        "source_count": len(evidence["sources"]),
                        "retrieval_seconds": dense_seconds
                        if arm == "vector_rag"
                        else graph_seconds,
                        "graph_truncated": view.truncation if arm == "domain_kg" else False,
                        "path_hops": [len(p) for p in evidence["paths"]],
                    },
                }
            )
    return packets


def evidence_at_k(packet, k):
    if k not in q.KS:
        raise ValueError("unplanned retrieval cutoff")
    return {**packet, "evidence": {"sources": packet["evidence"]["sources"][:k], "paths": []}}


def job(kind, payload):
    # Blind model requests contain no experiment arm or opaque case identifiers.
    return {"kind": kind, "key": digest(payload), "request": payload}


def read_results(root):
    results = {}
    for path in (root / "assessments").glob("*.json"):
        row = read(path)
        if row["request_sha256"] != path.stem:
            raise ValueError("result request identity changed")
        results[path.stem] = row
    return results


def response(results, payload):
    row = results.get(digest(payload))
    if row is None:
        return None, "missing"
    if row.get("error"):
        return None, row["error"]
    return row.get("content"), None


def reader_result(packet, results):
    raw, error = response(results, q.reader_request(packet))
    if error:
        return None, error
    return q.parse_reader(raw, {s["id"] for s in packet["evidence"]["sources"]}), None


def qa_jobs(packet, reader, *, judge=q.JUDGE, separate_grounding=False):
    if separate_grounding:
        return [
            job("qa_correctness", q.correctness_request(packet, reader, judge=judge)),
            job("qa_support", q.grounding_request(packet, reader, judge=judge)),
        ]
    return [job("qa", q.qa_request(packet, reader, judge=judge))]


def assessment_result(payload, results, parse, *, bounded_review=False):
    judgments, errors = [], []
    for request in (payload, q.review_request(payload)) if bounded_review else (payload,):
        raw, error = response(results, request)
        value = None
        if not error:
            try:
                value = parse(raw)
            except ValueError:
                error = "invalid_protocol"
        judgments.append(value)
        if error:
            errors.append(error)
    assessed = q.reviewed_assessment(*judgments) if bounded_review else judgments[0]
    if assessed and assessed.get("review_disagreements"):
        errors.append("review_disagreement")
    return assessed, ",".join(errors) or None


def qa_result(
    packet, reader, results, *, judge=q.JUDGE, separate_grounding=False, bounded_review=False
):
    jobs = qa_jobs(packet, reader, judge=judge, separate_grounding=separate_grounding)
    parsers = (q.parse_correctness, q.parse_support) if separate_grounding else (q.parse_qa,)
    combined, errors, unknowns, component_unknowns = {}, [], set(), set()
    for current, parse in zip(jobs, parsers, strict=True):
        value, error = assessment_result(
            current["request"],
            results,
            lambda raw, parse=parse: parse(raw, packet, reader),
            bounded_review=bounded_review,
        )
        if error:
            errors.append(current["kind"] + ":" + error)
        if value:
            unknowns.update(value.get("metric_unknowns", []))
            component_unknowns.update(value.get("component_unknowns", []))
            if current["kind"] == "qa_support":
                value = {**value, "support_reason": value["reason"]}
                value.pop("reason")
            combined.update(value)
    if unknowns:
        combined["metric_unknowns"] = sorted(unknowns)
        combined["review_disagreements"] = sorted(unknowns)
    if component_unknowns:
        combined["component_unknowns"] = sorted(component_unknowns)
    return combined or None, ";".join(errors) or None


def jobs_for(
    phase, packets, results, *, judge=q.JUDGE, separate_grounding=False, bounded_review=False
):
    jobs = {}
    for packet in packets:
        if packet.get("retrieval_error"):
            continue
        if phase == "answer":
            current = [job("reader", q.reader_request(packet))]
        elif phase == "evidence":
            current = [
                job("evidence", q.evidence_request(evidence_at_k(packet, k), judge=judge))
                for k in q.KS
            ]
        elif phase == "judge":
            reader, error = reader_result(packet, results)
            if error:
                continue
            current = qa_jobs(packet, reader, judge=judge, separate_grounding=separate_grounding)
        else:
            raise ValueError("unknown assessment phase")
        for entry in add_reviews(current, bounded_review and phase != "answer"):
            jobs[entry["key"]] = entry
    return sorted(jobs.values(), key=lambda j: j["key"])


def calibration_cases():
    return read(CALIBRATION)


def add_reviews(jobs, enabled):
    return (
        jobs + [job(j["kind"] + "_review", q.review_request(j["request"])) for j in jobs]
        if enabled
        else jobs
    )


def calibration_jobs(fixtures, *, judge=q.JUDGE, separate_grounding=False, bounded_review=False):
    jobs = []
    for fixture in fixtures:
        if fixture["kind"] == "evidence":
            jobs.append(job("evidence", q.evidence_request(fixture["packet"], judge=judge)))
        else:
            jobs.extend(
                qa_jobs(
                    fixture["packet"],
                    fixture["reader"],
                    judge=judge,
                    separate_grounding=separate_grounding,
                )
            )
    return add_reviews(jobs, bounded_review)


def calibration_report(
    fixtures, results, *, judge=q.JUDGE, separate_grounding=False, bounded_review=False
):
    rows = []
    for fixture in fixtures:
        parsed = None
        if fixture["kind"] == "evidence":
            parsed, error = assessment_result(
                q.evidence_request(fixture["packet"], judge=judge),
                results,
                lambda raw, packet=fixture["packet"]: q.parse_evidence(raw, packet),
                bounded_review=bounded_review,
            )
        else:
            parsed, error = qa_result(
                fixture["packet"],
                fixture["reader"],
                results,
                judge=judge,
                separate_grounding=separate_grounding,
                bounded_review=bounded_review,
            )
        passed = (
            parsed is not None
            and error is None
            and all(parsed.get(k) == v for k, v in fixture["expected"].items())
        )
        rows.append(
            {
                "id": fixture["id"],
                "passed": passed,
                "error": error,
                "expected": fixture["expected"],
                "actual": parsed,
            }
        )
    return {
        "passed": bool(rows) and all(r["passed"] for r in rows),
        "unknown": sum(r["error"] is not None for r in rows),
        "rows": rows,
        "scope": "synthetic regression gate, not proof of real-corpus judge accuracy",
    }


def score_packets(
    packets, results, *, judge=q.JUDGE, separate_grounding=False, bounded_review=False
):
    rows = []
    for packet in packets:
        errors, qa, assessments, at_k = [], None, {}, {}
        retrieval_error = packet.get("retrieval_error")
        packet_results = {} if retrieval_error else results
        if retrieval_error:
            errors.append("retrieval_" + retrieval_error)
        reader, error = reader_result(packet, packet_results)
        if error:
            errors.append("reader_" + error)
        for k in q.KS:
            candidate = evidence_at_k(packet, k)
            assessment, error = assessment_result(
                q.evidence_request(candidate, judge=judge),
                packet_results,
                lambda raw, candidate=candidate: q.parse_evidence(raw, candidate),
                bounded_review=bounded_review,
            )
            if error:
                errors.append(f"evidence_at_{k}_" + error)
            assessments[str(k)] = assessment
            values = q.score(None, assessment, None)
            at_k[str(k)] = {m: values[m] for m in q.RETRIEVAL_METRICS}
        if reader is not None:
            qa, error = qa_result(
                packet,
                reader,
                packet_results,
                judge=judge,
                separate_grounding=separate_grounding,
                bounded_review=bounded_review,
            )
            if error:
                errors.append("qa_" + error)
        else:
            errors.append("qa_no_reader")
        rows.append(
            {
                "id": packet["id"],
                "model": packet["model"],
                "arm": packet["arm"],
                "reader": reader,
                "evidence_judgments": assessments,
                "qa_judgment": qa,
                "errors": errors,
                "retrieval_at_k": at_k,
                "scores": q.score(reader, assessments["12"], qa),
            }
        )
    return rows


async def execute(root, jobs, key):
    runner = CloudCalls(root / "calls", key)
    async with cloud_client() as client:

        async def work(current):
            path = root / "assessments" / (current["key"] + ".json")
            if path.exists():
                return
            row = await runner.chat(client, current["request"])
            write_new(path, {**row, "kind": current["kind"], "request_sha256": current["key"]})

        await pool(jobs, work, concurrency=4)


def report(root):
    packets, results = read(root / "packets.json"), read_results(root)
    judge = selected_judge(root)
    separate_grounding = uses_separate_grounding(root)
    rows = score_packets(packets, results, **assessment_options(root))
    models = selected_models(root)
    summary = q.summarize(read(root / "cases.json"), rows, models=models)
    summary["judge"] = judge
    summary["separate_grounding"] = separate_grounding
    summary["calibration"] = calibration_report(
        calibration_cases(), results, **assessment_options(root)
    )
    summary["semantic_ready"] = (
        summary["calibration"]["passed"]
        and len(rows) == len(read(root / "cases.json")) * len(models) * len(q.ARMS)
        and all(
            all(
                a is not None and q.score(None, a, None)["evidence_sufficient"] is not None
                for a in r["evidence_judgments"].values()
            )
            and r["scores"]["grounded_correct"] is not None
            for r in rows
        )
    )
    summary["graph_statistics"] = {
        m: read(root / "construction" / m / "graph-statistics.json") for m in models
    }
    summary["inventory"] = read(root / "provenance.json")
    preflight_path = root / "preflight" / "summary.json"
    summary["preflight"] = read(preflight_path) if preflight_path.exists() else None
    summary["assessment_hashes"] = {
        p.name: file_hash(p) for p in (root / "assessments").glob("*.json")
    }
    summary["performance"] = performance(root, packets)
    for name, value in (("scored-rows.json", rows), ("summary.json", summary)):
        (root / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    lines = [
        "# KG quality — remote API only",
        "",
        "Semantic judgments are provisional; missing judgments remain unknown.",
        f"Semantic judge: `{judge}`.",
        "",
        "| Model | Method | Evidence sufficient@12 | Semantic coverage@12 | Source precision@12 | "
        "Semantic correct | Grounded correct | Completeness | Citation support | "
        "Unsupported claims | Known grounded QA / planned |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    metrics = [
        *q.RETRIEVAL_METRICS,
        "semantic_correct",
        "grounded_correct",
        "answer_completeness",
        "citation_supported",
        "unsupported_claim_rate",
    ]
    for model, entry in summary["models"].items():
        for arm, value in entry["groups"]["all"]["arms"].items():
            values = [
                "unknown"
                if value[m]["mean_known"] is None
                else f"{100 * value[m]['mean_known']:.1f}% "
                f"(known {value[m]['known']}/{value[m]['planned']})"
                for m in metrics
            ]
            qa = value["grounded_correct"]
            lines.append(
                f"| {model} | {arm} | "
                + " | ".join(values)
                + f" | {qa['known']} / {qa['planned']} |"
            )
    lines += [
        "",
        "Every metric's denominator, all-planned bounds, paired changes, per-dataset/track "
        "results, semantic @1/3/5/10/12, API errors and graph structure are in summary.json.",
        (
            "Both arms use identical semantic source reranking and original-only reader context. "
            "KG adds graph recall; graph seed retrieval includes lexical matching. "
            if frozen_bool(root, "source_merge")
            else "This compares dense + BM25 + graph with dense RAG; it does not isolate KG alone."
        ),
    ]
    (root / "report.md").write_text("\n".join(lines) + "\n")
    return summary


def performance(root, packets):
    def distribution(values):
        return {
            "count": len(values),
            "p50": float(np.percentile(values, 50)) if values else None,
            "p95": float(np.percentile(values, 95)) if values else None,
        }

    def usage(directory):
        attempts = [read(p) for p in directory.rglob("*.result.json")]
        usages = [(r.get("response") or {}).get("usage") for r in attempts]
        return {
            "attempt_seconds": distribution([r["seconds"] for r in attempts if "seconds" in r]),
            "attempts": len(attempts),
            "errors": dict(Counter(r["error"] for r in attempts if r.get("error"))),
            "reported_tokens": {
                field: sum(u.get(field, 0) for u in usages if isinstance(u, dict))
                for field in ("prompt_tokens", "completion_tokens", "total_tokens")
            },
            "attempts_without_usage": sum(not isinstance(u, dict) for u in usages),
            "ambiguous_launches": sum(
                not p.with_name(p.name.replace(".launch.", ".result.")).exists()
                for p in directory.rglob("*.launch.json")
            ),
        }

    return {
        "retrieval_wall_seconds_including_merge_and_cache": {
            model: {
                arm: distribution(
                    [
                        p["diagnostics"]["retrieval_seconds"]
                        for p in packets
                        if p["model"] == model and p["arm"] == arm
                    ]
                )
                for arm in q.ARMS
            }
            for model in selected_models(root)
        },
        "construction": {
            model: usage(root / "construction" / model / "calls") for model in selected_models(root)
        },
        "shared_embedding": usage(root / "embedding-calls"),
        "source_merge": usage(root / "merge-calls"),
        "answer_and_judge": usage(root / "calls"),
        "preflight": usage(root / "preflight"),
        "cost": None,
        "note": "Provider usage only; no verified tariff, DB latency or throughput claim. "
        "Identical requests are deduplicated, not independent repeats. Construction "
        "includes the production bounded evidence-correction and source-splitting calls.",
    }


async def preflight(root, key):
    directory = root / "preflight"
    models = selected_models(root)
    manifest = await catalog(key, models=models)
    save_once(directory / "catalog.json", manifest)
    missing = set(models) - set(manifest["ids"])
    if missing:
        raise ValueError("working models absent from API catalog: " + ", ".join(sorted(missing)))
    rows = {}
    async with cloud_client() as client:
        calls = CloudCalls(directory / "calls", key)
        for model in selected_models(root):
            packet = {
                "model": model,
                "question": "Where was Ada born?",
                "evidence": {
                    "sources": [{"id": "s1", "text": "Ada was born in Rome."}],
                    "paths": [],
                },
            }
            row = await calls.chat(client, q.reader_request(packet))
            valid = not row["error"] and q.parse_reader(row["content"], {"s1"})["protocol_valid"]
            construction = await construct(root, model, key, probe=True)
            rows[model] = {
                "reader_protocol": bool(valid),
                "reader_error": row["error"],
                "construction": construction,
            }
            print(json.dumps({"preflight_model": model, **rows[model]}), flush=True)
        await embed(directory, ["Cloud embedding capability check."], client, key)
    result = {
        "passed": all(r["reader_protocol"] and r["construction"]["passed"] for r in rows.values()),
        "models": rows,
        "embedding": "passed",
        "scope": "one source and reader request per model",
    }
    save_once(directory / "summary.json", result)
    return result


def require_gates(root):
    if read(root / "preflight" / "summary.json")["embedding"] != "passed":
        raise ValueError("shared embedding capability gate failed")
    if not calibration_report(
        calibration_cases(),
        read_results(root),
        **assessment_options(root),
    )["passed"]:
        raise ValueError("judge calibration failed; inspect before running the matrix")


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase",
        choices=(
            "draft",
            "freeze",
            "preflight",
            "construct",
            "rank",
            "retrieve",
            "calibrate",
            "answer",
            "evidence",
            "judge",
            "report",
            "structure",
        ),
    )
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--model", choices=WORK_MODELS)
    parser.add_argument("--models", nargs="+", choices=WORK_MODELS, default=WORK_MODELS)
    parser.add_argument("--judge", choices=WORK_MODELS, default=q.JUDGE)
    parser.add_argument("--separate-grounding", action="store_true")
    parser.add_argument("--bounded-review", action="store_true")
    parser.add_argument("--source-merge", action="store_true")
    parser.add_argument(
        "--construction-semantics", choices=CONSTRUCTION_SEMANTICS, default="source_roles_v1"
    )
    parser.add_argument("--graph-arm", choices=("baseline", "revised"), default="revised")
    parser.add_argument("--cached-ranks-from", type=Path)
    parser.add_argument("--secret-file", type=Path)
    args = parser.parse_args()
    if args.phase in ("draft", "freeze", "structure"):
        if args.input is None:
            parser.error("--input is required")
        if args.phase == "draft":
            draft(args.input, args.root)
        elif args.phase == "freeze":
            if args.reviews is None:
                parser.error("--reviews is required")
            freeze(
                args.input,
                args.reviews,
                args.root,
                models=tuple(args.models),
                construction_semantics=args.construction_semantics,
                judge=args.judge,
                separate_grounding=args.separate_grounding,
                bounded_review=args.bounded_review,
                source_merge=args.source_merge,
            )
        else:
            graph = load_graph(args.input, args.graph_arm)
            write_new(
                args.root / (args.graph_arm + "-structure.json"),
                graph_statistics(read(args.input / "corpus.json"), graph),
            )
        return
    verify(args.root)
    with run_lock(args.root):
        if args.phase == "preflight":
            result = await preflight(args.root, cloud_key(args.secret_file))
            print(json.dumps(result))
        elif args.phase == "calibrate":
            await execute(
                args.root,
                calibration_jobs(
                    calibration_cases(),
                    **assessment_options(args.root),
                ),
                cloud_key(args.secret_file),
            )
            result = calibration_report(
                calibration_cases(),
                read_results(args.root),
                **assessment_options(args.root),
            )
            print(json.dumps(result, ensure_ascii=False))
        elif args.phase in ("construct", "rank"):
            require_gates(args.root)
            if args.phase == "rank":
                if args.cached_ranks_from:
                    import_ranks(args.root, args.cached_ranks_from)
                else:
                    await rank(args.root, cloud_key(args.secret_file))
            else:
                if args.model is not None and args.model not in selected_models(args.root):
                    parser.error("--model is outside the frozen selection")
                for model in (args.model,) if args.model else selected_models(args.root):
                    await construct(args.root, model, cloud_key(args.secret_file))
                    names = sorted(
                        str(p.relative_to(args.root))
                        for p in (args.root / "construction" / model).rglob("*.json")
                    )
                    seal(args.root, ["frozen.json", *names], model + "-construction-frozen.json")
                    print(json.dumps({"constructed": model}), flush=True)
        elif args.phase == "retrieve":
            require_gates(args.root)
            for model in selected_models(args.root):
                verify(args.root, model + "-construction-frozen.json")
            if not (args.root / "packets.json").exists():
                from eval.kg_quality_merge import SourceMerger

                merge = (
                    SourceMerger(args.root, cloud_key(args.secret_file))
                    if frozen_bool(args.root, "source_merge")
                    else None
                )
                packets = []
                for model in selected_models(args.root):
                    packets.extend(
                        await retrieve_packets(
                            read(args.root / "cases.json"),
                            read(args.root / "corpus.json"),
                            read(args.root / "construction" / model / "graph.json"),
                            read(args.root / "ranks.json"),
                            model=model,
                            merge=merge,
                        )
                    )
                write_new(args.root / "packets.json", packets)
            seal(
                args.root,
                [
                    "frozen.json",
                    "ranks.json",
                    "packets.json",
                    *(m + "-construction-frozen.json" for m in selected_models(args.root)),
                ],
                "retrieval-frozen.json",
            )
        else:
            verify(args.root, "retrieval-frozen.json")
            for model in selected_models(args.root):
                verify(args.root, model + "-construction-frozen.json")
            if args.phase == "report":
                summary = report(args.root)
                print(
                    json.dumps(
                        {
                            "semantic_ready": summary["semantic_ready"],
                            "report": str(args.root / "report.md"),
                        }
                    )
                )
            else:
                require_gates(args.root)
                results = read_results(args.root)
                await execute(
                    args.root,
                    jobs_for(
                        args.phase,
                        read(args.root / "packets.json"),
                        results,
                        **assessment_options(args.root),
                    ),
                    cloud_key(args.secret_file),
                )


if __name__ == "__main__":
    asyncio.run(main())
