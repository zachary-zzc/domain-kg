"""Checkpointed public-source pilot; no accepted store writes or automatic HTTP retries."""

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path

import httpx

from eval.public_qa_run import bounded_call
from eval.source_role_eval import (
    ARMS,
    EXTRACTOR,
    JUDGE,
    blind_facts,
    digest,
    extraction_request,
    judgment_request,
    parse_facts,
    parse_judgments,
    parse_references,
    reference_request,
    select_sources,
)

ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
# Official Beijing list prices checked 2026-10-21; no free tier/cache discount assumed.
PRICES = {EXTRACTOR: (1.8, 10.8), JUDGE: (1.2, 7.2)}
PRICE_URLS = [
    "https://help.aliyun.com/zh/model-studio/qwen3-6-35b-a3b",
    "https://help.aliyun.com/zh/model-studio/qwen3-5-397b-a17b",
]
CODE = [
    "eval/source_role_eval.py",
    "eval/source_role_run.py",
    "eval/dual_path_data.py",
    "eval/public_qa_run.py",
    "src/domain_kg/providers/semantics.py",
]
DEVELOPMENT = [
    "d-97a9a8b332d1378ebbc209ac",  # performer versus creator
    "d-c488c795cc289ed7a71d4df1",  # tautological relation
    "d-f38e8e0b618952c3ed7f2b34",  # composition, not uses
    "d-7730b6d2f9d5107ffb0b0e02",  # attribution between multiple singers
    "d-e301807dae3d6baeacd3ded0",  # naturalization versus birth
    "d-e3f1f3375e916e23135ba9a1",  # political/time scope
    "d-2f812d90b2b965dfde83c5ae",  # supported author bridge
    "d-e03b3412a8c90fe1ab7e6740",  # author birth and alias
]


def read(path):
    return json.loads(path.read_text())


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive durable reservation precedes HTTP; ambiguous launches cannot be replayed.
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())


def code_hashes():
    return {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in CODE}


@contextmanager
def run_lock(root):
    """Prevent two runners from consuming one budget or judging unfinished calls."""
    with (root / "run.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def reserve_cost(payload):
    raw = json.dumps(payload, ensure_ascii=False).encode()
    model, output = payload["model"], payload["max_tokens"]
    if (
        len(raw) > 32000
        or model not in PRICES
        or type(output) is not int
        or not 1 <= output <= 6144
    ):
        raise ValueError("request exceeds priced pilot bounds")
    # UTF-8 bytes upper-bound ordinary text tokens; 1024 covers chat framing overhead.
    inp, out = PRICES[model]
    return ((len(raw) + 1024) * inp + output * out) / 1_000_000


class Ledger:
    def __init__(self, directory, max_calls=100, max_rmb=8):
        self.directory, self.max_calls, self.max_rmb = directory, max_calls, max_rmb
        directory.mkdir(parents=True, exist_ok=True)

    @property
    def launches(self):
        return [read(p) for p in sorted(self.directory.glob("*.launch.json"))]

    @property
    def reserved(self):
        return sum(r["reserved_rmb"] for r in self.launches)

    def paths(self, label):
        stem = digest(label)
        return (self.directory / (stem + ".launch.json"), self.directory / (stem + ".result.json"))

    def reserve(self, label, payload):
        launch, _ = self.paths(label)
        row = {
            "label": label,
            "request_sha256": digest(payload),
            "reserved_rmb": reserve_cost(payload),
            "model": payload["model"],
        }
        if launch.exists():
            if read(launch) != row:
                raise ValueError("checkpoint request changed")
            return False
        if (
            len(self.launches) >= self.max_calls
            or self.reserved + row["reserved_rmb"] > self.max_rmb
        ):
            raise ValueError("pilot budget reached")
        write_new(launch, row)
        return True

    async def call(self, client, label, payload, key):
        _, result_path = self.paths(label)
        fresh = self.reserve(label, payload)
        if not fresh:
            return read(result_path) if result_path.exists() else None
        response = await bounded_call(client, ENDPOINT, payload, key)
        row = {
            "label": label,
            "model": payload["model"],
            "content": None,
            "usage": None,
            "actual_rmb": None,
            "error": response["error"],
        }
        try:
            raw = response["response"]
            usage = raw["usage"]
            inp, out = usage["prompt_tokens"], usage["completion_tokens"]
            if any(type(n) is not int or not 0 <= n <= 1_000_000 for n in (inp, out)):
                raise ValueError("invalid usage")
            row["usage"] = {"prompt_tokens": inp, "completion_tokens": out}
            prices = PRICES[payload["model"]]
            row["actual_rmb"] = (inp * prices[0] + out * prices[1]) / 1_000_000
            choice = raw["choices"][0]
            message = choice["message"]
            if (
                row["error"]
                or choice["finish_reason"] != "stop"
                or message.get("refusal")
                or message.get("tool_calls")
                or message.get("function_call")
                or not isinstance(message.get("content"), str)
            ):
                raise ValueError("invalid completion")
            row["content"] = message["content"]
        except (ValueError, TypeError, KeyError, IndexError):
            row["error"] = row["error"] or "invalid_completion"
        if row["actual_rmb"] is not None and row["actual_rmb"] > reserve_cost(payload):
            row["error"] = "usage_exceeded_reservation"
            row["content"] = None
        write_new(result_path, row)
        print(
            json.dumps(
                {
                    "call": len(self.launches),
                    "label": label,
                    "error": row["error"],
                    "reserved_rmb": round(self.reserved, 4),
                }
            ),
            flush=True,
        )
        if row["error"] in {"http_401", "http_403", "usage_exceeded_reservation"}:
            raise ValueError("provider authorization or billing boundary failed; stopped")
        return row


def prepare(root, corpus, review_files):
    if root.exists():
        raise ValueError("choose a new output directory; existing experiments are immutable")
    reviewed = set()
    for path in review_files:
        reviewed.update(re.findall(r"[ds]-[0-9a-f]{24}", path.read_text()))
    documents = read(corpus)
    excluded = {
        doc["id"]
        for doc in documents
        if doc["id"] in reviewed or any(unit["id"] in reviewed for unit in doc["units"])
    }
    sources = select_sources(documents, DEVELOPMENT, excluded)
    root.mkdir(parents=True)
    write_new(root / "sources.json", sources)
    write_new(
        root / "frozen.json",
        {
            "corpus_sha256": hashlib.sha256(corpus.read_bytes()).hexdigest(),
            "review_files": {
                str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in review_files
            },
            "excluded_reviewed_sources": len(excluded),
            "sources_sha256": digest(sources),
            "code": code_hashes(),
            "prices": PRICES,
            "price_urls": PRICE_URLS,
            "limits": {"calls": 100, "reserved_rmb": 8, "max_request_bytes": 32000},
            "selection": "8 development sources; 3 SHA256-selected unseen sources per dataset",
            "reference_policy": "Independent source-only inventory before candidate extraction",
            "baseline": "Fresh matched single-source protocol, not replay of old batched baseline",
        },
    )


def content(row):
    if row is None or row["error"] or row["content"] is None:
        raise ValueError("missing or failed provider response")
    return row["content"]


async def execute_locked(root, key):
    with run_lock(root):
        await execute(root, key)


async def execute(root, key):
    frozen, sources = read(root / "frozen.json"), read(root / "sources.json")
    if frozen["code"] != code_hashes() or frozen["sources_sha256"] != digest(sources):
        raise ValueError("experiment code or sources changed")
    ledger = Ledger(root / "calls")
    async with httpx.AsyncClient(
        trust_env=False,
        follow_redirects=False,
        timeout=None,
        limits=httpx.Limits(max_connections=1),
    ) as client:
        for doc in sources:
            result_path = root / "documents" / (doc["id"] + ".json")
            if result_path.exists():
                continue
            result = {
                "id": doc["id"],
                "split": doc["split"],
                "dataset": doc["dataset"],
                "arms": {},
                "errors": {},
                "references": [],
                "judgments": None,
            }
            row = await ledger.call(client, doc["id"] + ":reference", reference_request(doc), key)
            try:
                result["references"] = parse_references(content(row), doc)
            except ValueError:
                result["errors"]["reference"] = "invalid_or_missing_reference"
            for arm, limit in ARMS.items():
                row = await ledger.call(
                    client, doc["id"] + ":" + arm, extraction_request(doc, arm), key
                )
                try:
                    result["arms"][arm] = parse_facts(content(row), doc, limit)
                except ValueError:
                    result["errors"][arm] = "invalid_or_missing_extraction"
            facts, mapping = blind_facts(doc, result["arms"])
            result["fact_mapping"] = mapping
            row = await ledger.call(
                client,
                doc["id"] + ":judge",
                judgment_request(doc, facts, result["references"]),
                key,
            )
            try:
                result["judgments"] = parse_judgments(
                    content(row), set(mapping), {r["id"] for r in result["references"]}
                )
            except ValueError:
                result["errors"]["judge"] = "invalid_or_missing_judgment"
            write_new(result_path, result)


def summarize(root):
    sources = read(root / "sources.json")
    rows = [read(p) for p in (root / "documents").glob("*.json")]
    groups = {}
    for split in ("development", "holdout"):
        for arm in ARMS:
            selected = [r for r in rows if r["split"] == split]
            stats = {
                "planned_documents": sum(d["split"] == split for d in sources),
                "completed_documents": len(selected),
                "valid_extractions": 0,
                "judged_documents": 0,
                "facts": 0,
                "supported": 0,
                "contradicted": 0,
                "insufficient": 0,
                "uninformative": 0,
                "reference_units": 0,
                "covered_reference_units": 0,
                "source_validated_facts": 0,
            }
            for row in selected:
                stats["valid_extractions"] += arm in row["arms"]
                stats["source_validated_facts"] += len(row["arms"].get(arm, []))
                if row["judgments"] is None:
                    continue
                stats["judged_documents"] += 1
                valid_refs = {
                    r["id"] for r in row["judgments"]["references"] if r["verdict"] == "supported"
                }
                # Invalid extraction is zero coverage, never silently removed from denominator.
                stats["reference_units"] += len(valid_refs)
                covered = set()
                for fact in row["judgments"]["facts"]:
                    if row["fact_mapping"][fact["id"]]["arm"] != arm:
                        continue
                    stats["facts"] += 1
                    stats[fact["verdict"]] += 1
                    if fact["verdict"] == "supported":
                        covered.update(set(fact["matches"]) & valid_refs)
                stats["covered_reference_units"] += len(covered)
            stats["provisional_precision"] = (
                stats["supported"] / stats["facts"] if stats["facts"] else None
            )
            stats["provisional_reference_coverage"] = (
                stats["covered_reference_units"] / stats["reference_units"]
                if stats["reference_units"]
                else None
            )
            groups[split + ":" + arm] = stats
    ledger = Ledger(root / "calls")
    responses = [read(p) for p in (root / "calls").glob("*.result.json")]
    return {
        "complete_comparison": len(rows) == len(sources) and all(not r["errors"] for r in rows),
        "groups": groups,
        "attempts": len(ledger.launches),
        "reserved_rmb": ledger.reserved,
        "known_actual_rmb": sum(r["actual_rmb"] or 0 for r in responses),
        "unknown_usage_attempts": len(ledger.launches)
        - sum(r["usage"] is not None for r in responses),
        "limitations": [
            "Model-generated reference inventory and same-family judge; provisional",
            "No QA exact match; no downstream answer or end-to-end KG score",
            "Incomplete documents/judges are explicitly reported, not accuracy zero",
            "Coverage arm has a larger fact cap; token ceiling is equal",
        ],
    }


def credential(path):
    if path is None:
        key = os.environ.get("DASHSCOPE_API_KEY", "")
    else:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("credential must be an owner-only regular file")
        key = path.read_text().strip()
    if not key:
        raise ValueError("DASHSCOPE_API_KEY or --secret-file required")
    return key


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("prepare", "run", "summary"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--secret-file", type=Path)
    args = parser.parse_args()
    if args.phase == "prepare":
        base = Path(".cache/scoped-retrieval-2026-10-21-v5")
        prepare(
            args.root,
            Path(".cache/dual-path-2026-10-20-v3a/corpus.json"),
            [
                Path(".cache/scoped-construction-review-2026-10-21.json"),
                base / "semantic-packets.json",
                base / "path-audit-packets.json",
                Path(".cache/dual-path-2026-10-20-v3a/full-semantic-review/packets.json"),
                Path(".cache/dual-path-2026-10-20-v3a/semantic-review/packets.json"),
            ],
        )
    elif args.phase == "run":
        try:
            asyncio.run(execute_locked(args.root, credential(args.secret_file)))
        finally:
            print(json.dumps(summarize(args.root), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(summarize(args.root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
