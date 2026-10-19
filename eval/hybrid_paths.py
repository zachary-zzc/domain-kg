"""Fifth and final exploratory strategy: name anchors and bounded seeded paths.

This follows inspection of v4 rankings, so these questions are no longer an
independent test of this revision. Reuses v4 inputs and byte-identical answers.
"""

import asyncio
import json
import math
import re
import time
from pathlib import Path

from eval import hybrid_run as base
from eval.hybrid_audit import assess
from eval.hybrid_pilot import build_graph, fit_context, retrieve, terms
from eval.iterative_extraction import Iterations


def name_graph(chunks, facts=()):
    """English proper-token/identifier hints; not a trained NER or factual KG."""
    capitalized, prefixes = set(), set()
    for row in chunks:
        raw = re.findall(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", row["text"])
        for index, token in enumerate(raw):
            if any(c.isupper() for c in token):
                capitalized.update(terms(token))
                if index + 1 < len(raw) and raw[index + 1][0].isupper():
                    prefixes.update(terms(token))
    anchors = capitalized - prefixes
    masked = [
        {**row, "text": " ".join(t for t in terms(row["text"]) if t in anchors)} for row in chunks
    ]
    graph = build_graph(masked, facts)
    lengths = {row["id"]: max(1, len(terms(row["text"]))) for row in masked}
    return {
        left: {
            right: weight / math.sqrt(lengths[left] * lengths[right])
            for right, weight in neighbours.items()
        }
        for left, neighbours in graph.items()
    }


def walk(graph, seed, fallback, *, steps=2):
    if type(steps) is not int or not 0 <= steps <= 2 or seed not in graph:
        raise ValueError("Invalid path budget")
    selected, cursor = [seed], seed
    for _ in range(steps):
        candidates = [key for key in graph[cursor] if key not in selected]
        if not candidates:
            break
        cursor = min(candidates, key=lambda key: (-graph[cursor][key], key))
        selected.append(cursor)
    return list(dict.fromkeys(selected + fallback))[:3]


def retrieve_paths(chunks, embeddings, query_vector, query, arm, graph):
    baseline = retrieve(chunks, embeddings, query_vector, query, "A", graph)
    if arm == "A" or not baseline:
        return baseline
    ranking = walk(graph, baseline[0], baseline, steps=2)
    return fit_context(ranking, chunks, top_k=3, max_chars=1800)


async def run():
    parent = base.WORK
    original_manifest, corpus, config = base.frozen()
    if original_manifest["status"] != "completed_pilot_not_platform_acceptance":
        raise RuntimeError("Finish the initial fixed experiment before its exploratory successor")
    ledger = Iterations(parent.parent / "strategies.json")
    ledger.begin(
        5,
        hypothesis="Prevent seed reinforcement and generic-verb links: source-only "
        "proper-token anchors, length-normalized edges and a two-step seeded path; "
        "same embeddings, sources, 4B prompt and total 48-call budget",
    )
    destination = parent.parent / "4b-v5"
    if destination.exists():
        raise RuntimeError("Final strategy already exists; no reset or sixth strategy")
    destination.mkdir(mode=0o700)
    inputs = {
        name: base.read(name)
        for name in (
            "corpus.json",
            "embeddings.json",
            "extraction.json",
            "review.json",
            "native-manifest.json",
            "budget.json",
        )
    }
    previous_answers = base.read("answers.json")
    base.WORK = destination
    for name, value in inputs.items():
        base.save(name, value)
    manifest = {
        "status": "frozen_exploratory",
        "strategy": 5,
        "parent": str(parent),
        "parent_manifest_sha256": base.digest(parent / "manifest.json"),
        "files": {
            **original_manifest["files"],
            "eval/hybrid_paths.py": base.digest(Path(__file__)),
            "eval/hybrid_audit.py": base.digest(Path(__file__).with_name("hybrid_audit.py")),
        },
        "parent_artifacts": {name: base.digest(parent / name) for name in inputs},
        "annotation": "Exploratory after observing v4 rankings; provisional author labels",
        "limits": {
            "top_k": 3,
            "context_chars": 1800,
            "path_steps": 2,
            "cumulative_chat_calls": 48,
            "cumulative_chat_seconds": 1800,
        },
    }
    base.save("manifest.json", manifest)
    facts = base.reviewed_facts(inputs["extraction.json"]["candidates"], inputs["review.json"])
    tick = time.monotonic()
    graphs = {"B": name_graph(corpus), "C": name_graph(corpus, facts)}
    graph_seconds = time.monotonic() - tick
    base.save("graphs.json", {**graphs, "facts": facts, "build_seconds": graph_seconds})
    vectors = inputs["embeddings.json"]
    matrix = [vectors["chunk/" + c["id"]] for c in corpus]
    texts = {c["id"]: c["text"] for c in corpus}
    records = []
    for question in config["queries"]:
        for arm in ("A", "B", "C"):
            selected = retrieve_paths(
                corpus,
                matrix,
                vectors["query/" + question["id"]],
                question["query"],
                arm,
                graphs.get(arm, graphs["B"]),
            )
            records.append(
                {
                    "id": question["id"],
                    "split": question["split"],
                    "arm": arm,
                    "selected": selected,
                    "context_chars": sum(len(texts[k]) for k in selected),
                    "error": "not_run",
                    "metrics": assess(question, selected, None),
                }
            )
    base.save("retrievals.json", records)
    cache = {
        r["request_sha256"]: {
            "answer": r["answer"],
            "error": r["error"],
            "seconds": r["seconds"],
            "cache_origin": "v4",
        }
        for r in previous_answers
        if not r.get("error")
    }
    async with base.tracked_model("chat", "paths-answer") as guard:
        calls = base.calls_for(guard)
        try:
            for index, question in enumerate(config["queries"]):
                arms = ["A", "B", "C"]
                arms = arms[index % 3 :] + arms[: index % 3]
                for arm in arms:
                    row = next(r for r in records if r["id"] == question["id"] and r["arm"] == arm)
                    request = base.answer_request(
                        calls.profile.extraction_model,
                        question["query"],
                        [{"id": key, "text": texts[key]} for key in row["selected"]],
                    )
                    key = base.fingerprint(request)
                    row["request_sha256"] = key
                    if key in cache:
                        outcome = cache[key]
                        row["cached_identical_request"] = True
                    else:
                        calls.case_id = question["id"] + "/" + arm
                        started = time.monotonic()
                        try:
                            answer = await calls.invoke("evidence_answer", request)
                            outcome = {"answer": answer, "error": None}
                        except Exception as error:
                            outcome = {"answer": None, "error": type(error).__name__}
                        outcome.update(
                            seconds=round(time.monotonic() - started, 3), cache_origin="v5"
                        )
                        cache[key] = outcome
                        row["cached_identical_request"] = False
                    row.update(outcome)
                    row["metrics"] = assess(question, row["selected"], row["answer"])
                    base.save("answers.json", records)
                    base.save("summary.json", base.summarize(records))
                    if calls.limits.consecutive_timeouts >= 2:
                        raise RuntimeError("Two consecutive timeouts; final strategy halted")
        finally:
            await calls.close()
            base.save_budget(calls)
            base.save("answers.json", records)
            base.save("summary.json", base.summarize(records))
    report = base.summarize(records)
    gain = any(
        report[a + "/held_out_synthetic"]["supported_correct"]
        > report["A/held_out_synthetic"]["supported_correct"]
        for a in ("B", "C")
    )
    ledger.finish(
        5,
        success=gain,
        result={
            "new_chat_requests": len(calls.records),
            "logical_answers": len(records),
            "supported_synthetic_gain": gain,
            "artifacts": "4b-v5",
            "exploratory": True,
        },
    )
    manifest["status"] = "completed_exploratory_not_independent_acceptance"
    base.save("manifest.json", manifest)
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "new_chat_calls": len(calls.records),
                "cumulative_chat_calls": calls.limits.calls,
                "synthetic_gain": gain,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(run())
