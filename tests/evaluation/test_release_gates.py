"""Opt-in local ranking report over genuine stores and explicitly synthetic providers."""
# ruff: noqa: F811

import asyncio
import hashlib
import json
import os
import platform
import subprocess
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from statistics import mean
from time import monotonic

import pytest
from test_retrieval_corpus import retrieval_corpus  # noqa: F401

from domain_kg.citations.service import CitationService
from domain_kg.retrieval.models import SearchRequest
from domain_kg.retrieval.service import RetrievalService
from eval.configuration import load
from eval.metrics import score

pytestmark = [
    pytest.mark.storage,
    pytest.mark.skipif(
        not os.environ.get("DOMAIN_KG_EVALUATION_REPORT"),
        reason="run python -m eval.run for the bounded local release evaluation",
    ),
]


async def test_local_retrieval_release_report(retrieval_corpus, monkeypatch):
    path = Path(os.environ["DOMAIN_KG_EVALUATION_CONFIG"])
    output = Path(os.environ["DOMAIN_KG_EVALUATION_REPORT"])
    config, cases, corpus = await asyncio.to_thread(load, path)
    publishing, reader, receipts = retrieval_corpus
    _, _, queue, coordinator, _, faults = publishing
    service = RetrievalService(queue, coordinator.providers)
    sources = {receipt.material_id: label for label, receipt in receipts.items()}
    generation_calls = 0

    async def forbidden(*args, **kwargs):
        nonlocal generation_calls
        generation_calls += 1
        pytest.fail("the bounded local evaluation forbids generative query/model calls")

    monkeypatch.setattr(service.queries, "keywords", forbidden)
    monkeypatch.setattr(service.queries, "rerank", forbidden)
    monkeypatch.setattr(coordinator.providers, "extract", forbidden)
    embedding_before = len(faults.get("embedding_inputs", []))
    results = []
    citation_count = 0
    for case in cases:
        for mode in config.modes:
            started = monotonic()
            found = await service.search(
                reader,
                SearchRequest(
                    kg_ids=reader.kg_ids,
                    query=case.query,
                    mode=mode,
                    top_k=config.top_k,
                    filters=case.filters,
                ),
            )
            latency = monotonic() - started
            labels = []
            for hit in found.results:
                assert hit.kg_id in reader.kg_ids
                assert hit.material_id in sources and sources[hit.material_id] != "hidden"
                labels.append(sources[hit.material_id])
                assert hit.citation_ids
                for identifier in hit.citation_ids:
                    citation = await CitationService(queue).resolve(reader, hit.kg_id, identifier)
                    assert citation.material_id == hit.material_id and citation.spans
                    assert all(span.quote in citation.text for span in citation.spans)
                    citation_count += 1
            if case.must_be_empty:
                assert not found.results
            if mode == "fast":
                assert all(
                    branch.status == "skipped"
                    for branch in found.branch_status
                    if branch.branch in ("graph", "rerank")
                )
            results.append(
                {
                    "case_id": case.id,
                    "split": case.split,
                    "category": case.category,
                    "mode": mode,
                    "latency_seconds": latency,
                    "returned_sources": labels,
                    "expected_sources": list(case.expected),
                    "metrics": score(labels, set(case.expected), top_k=config.top_k),
                }
            )
    summaries = {}
    for mode in config.modes:
        selected = [row for row in results if row["mode"] == mode and row["split"] == "held_out"]
        metrics = {}
        for key in ("recall", "precision", "ndcg", "reciprocal_rank"):
            values = [row["metrics"][key] for row in selected if row["metrics"][key] is not None]
            metrics[key] = mean(values) if values else None
        latencies = sorted(row["latency_seconds"] for row in selected)
        summaries[mode] = {
            "held_out_cases": len(selected),
            **metrics,
            "no_answer_false_positives": sum(row["metrics"]["false_positive"] for row in selected),
            "mean_latency_seconds": mean(latencies),
            "max_latency_seconds": max(latencies),
        }
    kg = await queue.database.kgs.find_one(
        {
            "org_id": str(reader.org_id),
            "id": str(reader.kg_ids[0]),
        }
    )
    report = {
        "version": 1,
        "scope": "local_synthetic_provider_correctness",
        "status": "passed",
        "created_at": datetime.now(UTC).isoformat(),
        "git_revision": (
            await asyncio.to_thread(
                subprocess.check_output,
                ["git", "rev-parse", "HEAD"],
                text=True,
            )
        ).strip(),
        "working_tree_diff_sha256": hashlib.sha256(
            await asyncio.to_thread(subprocess.check_output, ["git", "diff"])
        ).hexdigest(),
        "corpus_sha256": hashlib.sha256(corpus).hexdigest(),
        "config_sha256": hashlib.sha256(await asyncio.to_thread(path.read_bytes)).hexdigest(),
        "configuration": config.model_dump(mode="json"),
        "runtime": {"python": platform.python_version(), "platform": platform.platform()},
        "dependencies": {
            name: version(name)
            for name in (
                "domain-kg",
                "lightrag-hku",
                "mcp",
                "pymongo",
                "neo4j",
                "qdrant-client",
            )
        },
        "publication": {
            "revision": kg["published_revision"],
            "generation": kg["generation"],
            "manifest_id": kg["published_manifest_id"],
        },
        "provider": {"mode": "synthetic", "embedding_dimensions": 3},
        "calls": {
            "generative_query": generation_calls,
            "live_model": 0,
            "synthetic_query_embeddings": len(faults.get("embedding_inputs", []))
            - embedding_before,
        },
        "verified_source_citations": citation_count,
        "unauthorized_results": 0,
        "held_out_summaries": summaries,
        "cases": results,
        "remaining_gates": [
            "real_model_and_corpus_quality",
            "120_independently_reviewed_pilot_questions",
            "reranker_and_upstream_permission_equivalent_comparison",
            "external_oidc_and_mcp_client",
            "production_sizing_and_team_pilot",
        ],
    }
    await asyncio.to_thread(output.write_text, json.dumps(report, indent=2) + "\n")
