"""Explicit bounded real-model experiment; no downloads and no graph publication."""

import argparse
import asyncio
import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4, uuid5

from domain_kg.graph.schema import schema_for
from domain_kg.ingestion.extraction import Extractor
from domain_kg.ingestion.models import ExtractionContent, ParsedChunk, ParsedDocument, Span
from domain_kg.models import Namespace
from domain_kg.providers.profiles import ProviderConnection, ProviderProfile
from eval.recursive_loop import run_recursive
from eval.recursive_runtime import LocalCalls, case_chunk, claims, relation_score
from scripts.native_model import CACHE, ROOT, model

CASES = ROOT / "eval/recursive_cases.json"


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def native_settings(size):
    settings = json.loads((ROOT / "config/medical-model.approved.json").read_bytes())
    if size == "0.8b":
        small = json.loads((ROOT / "config/model-runtime.proposed.json").read_bytes())["llm"]
        settings.update(
            {
                key: small[key]
                for key in (
                    "repository",
                    "revision",
                    "filename",
                    "size_bytes",
                    "sha256",
                    "served_model_name",
                    "context_tokens",
                    "threads",
                    "parallel_slots",
                    "batch_tokens",
                    "microbatch_tokens",
                    "rss_watchdog_bytes",
                )
            }
        )
    settings["max_completion_tokens"] = 768
    settings["request_timeout_seconds"] = 120
    return settings


def load_sources():
    manifest = json.loads((ROOT / "config/medical-pdf-smoke.json").read_bytes())
    pdf = CACHE / "smoke-medical/child-diabetes-annual-review.pdf"
    parsed = pdf.with_name("parsed.json")
    if digest(pdf) != manifest["sha256"] or pdf.stat().st_size != manifest["size_bytes"]:
        raise RuntimeError("Cached approved public source differs")
    document = ParsedDocument.model_validate_json(parsed.read_bytes())
    if document.blob.sha256 != manifest["sha256"]:
        raise RuntimeError("Parsed source does not match approved original")
    parent = ParsedChunk(
        org_id=document.blob.org_id,
        kg_id=document.blob.kg_id,
        material_id=document.blob.material_id,
        version_id=document.blob.version_id,
        id=uuid5(document.blob.version_id, "recursive-parent-v1"),
        text="\n".join(block.text for block in document.blocks),
        spans=tuple(
            Span(location=b.location, start=0, end=len(b.text), quote=b.text)
            for b in document.blocks
        ),
        parser_version=document.parser_version,
        chunker_version="recursive-parent-v1",
    )
    return parent, {"pdf_sha256": digest(pdf), "parsed_sha256": digest(parsed)}


def summaries(records):
    result = {}
    for stage in ("baseline", "decomposed", "verified_0", "verified_1", "verified_2"):
        for split in ("all", "development", "known_positive_regression", "frozen_contrast"):
            selected = [r for r in records if split == "all" or r["split"] == split]
            scores, failures = [], 0
            for record in selected:
                data = record.get("stages", {}).get(stage)
                if data is None and stage.startswith("verified_"):
                    available = [
                        record["stages"][key]
                        for key in record.get("stages", {})
                        if key.startswith("verified_") and key < stage
                    ]
                    data = available[-1] if available else None
                if data is None:
                    failures += 1
                    # An unscorable failed case cannot vanish from the recall denominator.
                    # Keep failed_cases explicit; this is never a correct no-answer result.
                    scores.append(relation_score([], record["gold"]))
                    continue
                scores.append(data["score"])
            tp, fp, fn = (sum(s[k] for s in scores) for k in ("tp", "fp", "fn"))
            precision = tp / (tp + fp) if tp + fp else 0
            recall = tp / (tp + fn) if tp + fn else None
            result[f"{stage}/{split}"] = {
                "cases": len(selected),
                "scored_cases": len(scores) - failures,
                "failed_cases": failures,
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "precision": precision,
                "recall": recall,
                "f1": 2 * precision * recall / (precision + recall) if precision and recall else 0,
                "no_answer_fp_cases": sum(s["no_answer_fp"] for s in scores),
                "direction_errors": sum(s["direction_errors"] for s in scores),
                "polarity_errors": sum(s["polarity_errors"] for s in scores),
            }
    return result


async def run(size):
    settings = native_settings(size)
    parent, source_hashes = load_sources()
    dataset = json.loads(CASES.read_bytes())
    output = (
        ROOT
        / ".cache/recursive-kg"
        / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + f"-{size}-" + uuid4().hex[:8])
    )
    output.mkdir(parents=True, mode=0o700)
    manifest_path = output / "native-manifest.json"
    manifest_path.write_text(json.dumps(settings, indent=2) + "\n")
    manifest_path.chmod(0o600)
    namespace = Namespace(org_id=parent.org_id, kg_id=parent.kg_id)
    profile = ProviderProfile(
        id=uuid5(parent.kg_id, "recursive-v1/" + settings["sha256"]),
        connection_ref="owned-local",
        extraction_model=settings["served_model_name"],
        extraction_revision=settings["revision"],
        extraction_format="source_names_v1",
        embedding_model="not-called",
        embedding_dimensions=1024,
        allowed_namespaces=(namespace,),
        data_policy_approved=True,
        extraction_context_tokens=4096,
        max_completion_tokens=768,
        schema_in_context=False,
        timeout_seconds=120,
    )
    connection = ProviderConnection(
        ref="owned-local",
        endpoint=settings["endpoint"],
        api_key=(CACHE / "llm-api-key").read_text().strip(),
    )
    full_schema = schema_for(namespace, "medical")
    schema = full_schema.model_copy(
        update={
            "predicates": tuple(
                p for p in full_schema.predicates if p.name in dataset["predicate_subset"]
            ),
        }
    )
    revision = await asyncio.to_thread(subprocess.check_output, ["git", "rev-parse", "HEAD"])
    diff = await asyncio.to_thread(
        subprocess.check_output,
        [
            "git",
            "diff",
            "HEAD",
            "--",
            "eval",
            "scripts",
            "src",
            "tests/unit/test_recursive_extraction.py",
            "tests/unit/test_recursive_runtime.py",
        ],
    )
    report = {
        "status": "running",
        "scope": "experimental_three_predicate_source_extraction",
        "protocol": "recursive-v2-short-verdicts",
        "model": size,
        "model_sha256": settings["sha256"],
        "cases_sha256": digest(CASES),
        "source_hashes": source_hashes,
        "git_revision": revision.decode().strip(),
        "working_tree_diff_sha256": hashlib.sha256(diff).hexdigest(),
        "annotation": dataset["annotation"],
        "schema": schema.model_dump(mode="json"),
        "limits": {
            "calls": 48,
            "max_repairs": 2,
            "wall_seconds": 1800,
            "rss_bytes": settings["rss_watchdog_bytes"],
            "context": 4096,
            "threads": 2,
            "parallel_models": 1,
        },
        "graph_publications": 0,
        "records": [],
    }
    print(
        json.dumps({"output": str(output), "model": size, "cases_sha256": report["cases_sha256"]}),
        flush=True,
    )
    started = time.monotonic()
    calls = guard = None
    try:
        async with asyncio.timeout(1800), model("chat", manifest_path=manifest_path) as guard:
            calls = LocalCalls(profile, connection, guard, output)

            class Baseline:
                id = profile.id

                async def complete(self, chunk, domain_schema):
                    return await calls.baseline(chunk, domain_schema)

            for case in dataset["cases"]:
                calls.case_id = case["id"]
                chunk = case_chunk(case, parent)
                record = {
                    "id": case["id"],
                    "split": case["split"],
                    "gold": case["gold"],
                    "source": chunk.model_dump(mode="json"),
                    "stages": {},
                    "failures": [],
                }
                report["records"].append(record)
                for branch in ("baseline", "recursive"):
                    phase_started = time.monotonic()
                    first = len(calls.records)
                    try:
                        if branch == "baseline":
                            extracted = await Extractor(Baseline()).extract(chunk, schema)
                            value = ExtractionContent(
                                entities=extracted.entities, assertions=extracted.assertions
                            )
                            snapshots = {"baseline": value}
                        else:
                            result = await run_recursive(chunk, schema, calls)
                            snapshots = result.snapshots
                            record["recursive_trace"] = result.model_dump(mode="json")
                        for name, value in snapshots.items():
                            predicted = claims(value)
                            record["stages"][name] = {
                                "claims": predicted,
                                "score": relation_score(predicted, case["gold"]),
                            }
                    except Exception as error:
                        record["failures"].append(
                            {
                                "branch": branch,
                                "error": type(error).__name__,
                                "detail": str(error)[:240],
                            }
                        )
                    record[branch + "_cost"] = {
                        "seconds": round(time.monotonic() - phase_started, 3),
                        "calls": len(calls.records) - first,
                        "input_tokens": sum(
                            c.get("usage", {}).get("prompt_tokens", 0)
                            for c in calls.records[first:]
                        ),
                        "output_tokens": sum(
                            c.get("usage", {}).get("completion_tokens", 0)
                            for c in calls.records[first:]
                        ),
                    }
                    await guard.require_alive()
                    if calls.limits.consecutive_timeouts >= 2:
                        raise RuntimeError("Two consecutive local timeouts; experiment halted")
                (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
                print(
                    json.dumps(
                        {
                            "case_done": case["id"],
                            "failures": record["failures"],
                            "stages": record["stages"],
                        }
                    ),
                    flush=True,
                )
            report["status"] = (
                "completed_with_case_failures"
                if any(record["failures"] for record in report["records"])
                else "completed_experiment"
            )
    except BaseException as error:
        report["status"] = "incomplete_experiment"
        report["runtime_error"] = {"type": type(error).__name__, "detail": str(error)[:240]}
        raise
    finally:
        if calls:
            await calls.close()
            report["calls"] = calls.limits.calls
            report["call_count_definition"] = "attempted_local_http_calls_not_accepted_inferences"
            report["completed_model_responses"] = sum("usage" in c for c in calls.records)
            report["output_tokens"] = calls.limits.output_tokens
        if guard:
            report["peak_rss_bytes"] = guard.peak_rss_bytes
            report["owned_process_stopped"] = guard.process.returncode is not None
        report["seconds"] = round(time.monotonic() - started, 3)
        report["summaries"] = summaries(report["records"])
        path = output / "report.json"
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        path.chmod(0o600)
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "report": str(path),
                    "seconds": report["seconds"],
                    "calls": report.get("calls"),
                }
            ),
            flush=True,
        )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=("4b", "0.8b"), required=True)
    asyncio.run(run(parser.parse_args().model))
