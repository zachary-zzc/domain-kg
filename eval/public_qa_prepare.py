"""Freeze downloaded public sources, stratified cases and exact cloud payloads."""

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

from eval.public_qa_data import (
    COUNTS,
    MODELS,
    SEED,
    fingerprint,
    normalize_drop,
    normalize_hotpot,
    normalize_musique,
    normalize_squad,
    request_for,
    select_cases,
)


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    path = Path(path)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    path.chmod(0o600)


def main(root):
    raw = root / "raw"
    if (root / "formal.json").exists():
        raise ValueError("already prepared; refuse to overwrite frozen questions")
    # Isolated file reader; neither a project dependency nor an inference runtime.
    sys.path.insert(0, str((root / "reader").resolve()))
    import pyarrow.parquet as pq

    table = pq.read_table(raw / "hotpot-dev.parquet")
    hotpot = []
    for row in table.to_pylist():
        context = row["context"]
        support = row["supporting_facts"]
        hotpot.append(
            {
                "_id": row["id"],
                "question": row["question"],
                "answer": row["answer"],
                "type": row["type"],
                "level": row["level"],
                "context": list(zip(context["title"], context["sentences"], strict=True)),
                "supporting_facts": list(zip(support["title"], support["sent_id"], strict=True)),
            }
        )
    (raw / "hotpot-dev.json").write_text(
        json.dumps(hotpot, ensure_ascii=False, separators=(",", ":"))
    )
    del table
    cases = list(normalize_squad(json.loads((raw / "squad-dev.json").read_text())))
    source_errors = []
    for q in hotpot:
        try:
            cases.append(normalize_hotpot(q))
        except ValueError as exc:
            source_errors.append({"id": "hotpot:" + q["_id"], "reason": str(exc)})
    del hotpot
    with (raw / "musique-full-dev.jsonl").open() as f:
        cases.extend(normalize_musique(json.loads(line)) for line in f if line.strip())
    cases.extend(normalize_drop(json.loads((raw / "drop_dataset_dev.json").read_text())))
    eligible, exclusions = [], source_errors
    for c in cases:
        payload = request_for(MODELS[0], c)
        size = len(json.dumps(payload, ensure_ascii=False).encode())
        if size > 24000 or len(c["support"]) > 8:
            exclusions.append(
                {"id": c["id"], "reason": "context_or_citation_budget", "request_bytes": size}
            )
        else:
            eligible.append(c)
    if len({c["id"] for c in eligible}) != len(eligible):
        raise ValueError("duplicate source IDs")
    pilot_ids = set()
    for stratum in ("squad_answer", "squad_noanswer", "hotpot_bridge", "musique_4", "drop_number"):
        first = min(
            (c for c in eligible if c["stratum"] == stratum),
            key=lambda c: fingerprint([SEED, "pilot", c["id"]]),
        )
        pilot_ids.add(first["id"])
    pilot, formal = select_cases(eligible, COUNTS, pilot_ids)
    save(root / "pilot.json", pilot)
    save(root / "formal.json", formal)
    save(root / "exclusions.json", exclusions)
    requests = {}
    for model in MODELS:
        requests[model] = {c["id"]: fingerprint(request_for(model, c)) for c in pilot + formal}
    save(root / "request-hashes.json", requests)
    bundle_files = [
        "squad-train.json",
        "squad-dev.json",
        "hotpot-dev.json",
        "musique-full-dev.jsonl",
        "drop_dataset_train.json",
        "drop_dataset_dev.json",
    ]
    files = [
        {
            "file": f"raw/{name}",
            "bytes": (raw / name).stat().st_size,
            "sha256": file_hash(raw / name),
        }
        for name in bundle_files
    ]
    size = sum(f["bytes"] for f in files)
    if not 180_000_000 <= size <= 220_000_000:
        raise ValueError(f"source bundle size {size} outside approximate 200 MB range")
    info = {
        "seed": SEED,
        "source_bundle_bytes": size,
        "source_files": files,
        "source_counts": dict(Counter(c["dataset"] for c in cases)),
        "eligible_counts": dict(Counter(c["stratum"] for c in eligible)),
        "formal_counts": dict(Counter(c["stratum"] for c in formal)),
        "pilot": len(pilot),
        "formal": len(formal),
        "excluded": len(exclusions),
        "models": MODELS,
        "planned_calls": len(formal) * len(MODELS),
        "request_bytes_per_model": sum(
            len(json.dumps(request_for(MODELS[0], c), ensure_ascii=False).encode()) for c in formal
        ),
        "scope": "identical supplied-context QA; no retrieval/KG construction",
        "licenses": {
            "squad": "CC-BY-SA-4.0",
            "hotpot": "CC-BY-SA-4.0",
            "musique": "CC-BY-4.0",
            "drop": "CC-BY-SA-4.0",
        },
        "upstream": {
            "squad": "https://rajpurkar.github.io/SQuAD-explorer/",
            "hotpot": "https://hotpotqa.github.io/",
            "musique": "https://github.com/StonyBrookNLP/musique",
            "drop": "https://github.com/allenai/allennlp-website/blob/master/drop.html",
        },
        "contamination": (
            "Public benchmarks; unseen in our optimization, pretraining overlap unknown"
        ),
    }
    save(root / "data-manifest.json", info)
    print(json.dumps(info, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(".cache/public-qa-2026-10-19"))
    main(parser.parse_args().root)
