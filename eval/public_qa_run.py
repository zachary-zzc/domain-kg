"""Cloud-only, bounded, checkpointed supplied-context public QA comparison."""

import argparse
import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from eval.public_qa_data import MODELS, fingerprint, request_for
from eval.public_qa_metrics import aggregate, parse_completion, score_case
from eval.public_qa_prepare import file_hash, save

ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
# Beijing list prices RMB / 1M tokens, checked 2026-10-19; no free quota assumed.
PRICES = {
    MODELS[0]: (1.2, 7.2),
    MODELS[1]: (0.8, 6.4),
    MODELS[2]: (1.8, 10.8),
    MODELS[3]: (3.0, 12.0),
    MODELS[4]: (0.0, 0.0),
}


def verify_frozen(root, files):
    for name, expected in files.items():
        if file_hash(root / name) != expected:
            raise ValueError(f"frozen artifact changed: {name}")


@dataclass
class Budget:
    max_calls: int = 5030
    max_input: int = 30_000_000
    max_output: int = 5_000_000
    max_cost: float = 100.0
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    unknown_usage: int = 0
    unknown_priced_usage: int = 0
    reserved_unknown_cost: float = 0.0

    def add(self, model, usage):
        self.calls += 1
        if not isinstance(usage, dict) or any(
            type(usage.get(k)) is not int or usage[k] < 0
            for k in ("prompt_tokens", "completion_tokens")
        ):
            self.unknown_usage += 1
            if any(PRICES[model]):
                self.unknown_priced_usage += 1
                self.reserved_unknown_cost += 1.0
            return
        inp, out = usage["prompt_tokens"], usage["completion_tokens"]
        self.input_tokens += inp
        self.output_tokens += out
        pi, po = PRICES[model]
        self.cost += (inp * pi + out * po) / 1_000_000

    def reason(self):
        for hit, reason in (
            (self.calls >= self.max_calls, "call_limit"),
            (self.input_tokens >= self.max_input, "input_token_limit"),
            (self.output_tokens >= self.max_output, "output_token_limit"),
            (self.cost + self.reserved_unknown_cost >= self.max_cost - 0.5, "cost_limit"),
        ):
            if hit:
                return reason
        return None


def eligible_models(rows, models):
    result = []
    for model in models:
        matches = [r for r in rows if r["model"] == model]
        if (
            len(matches) == 5
            and len({r["id"] for r in matches}) == 5
            and all(r["error"] is None for r in matches)
        ):
            result.append(model)
    return result


def remaining_cases(cases, rows, model):
    done = [r["id"] for r in rows if r["model"] == model]
    if len(done) != len(set(done)):
        raise ValueError("duplicate prior result")
    if not set(done) <= {c["id"] for c in cases}:
        raise ValueError("unplanned prior result")
    return [c for c in cases if c["id"] not in set(done)]


async def bounded_call(client, endpoint, payload, key, deadline=60):
    start = time.monotonic()
    result = {"response": None, "error": None, "http_status": None}
    try:
        async with asyncio.timeout(deadline):
            async with client.stream(
                "POST", endpoint, json=payload, headers={"Authorization": "Bearer " + key}
            ) as response:
                result["http_status"] = response.status_code
                body = bytearray()
                async for part in response.aiter_bytes():
                    body.extend(part)
                    if len(body) > 2 * 1024 * 1024:
                        raise ValueError("response_size_limit")
                text = bytes(body).decode("utf-8").replace(key, "[REDACTED]")
                try:
                    result["response"] = json.loads(text)
                except ValueError:
                    result["response_text"] = text
                    result["error"] = "invalid_response_json"
                if response.status_code != 200:
                    result["error"] = f"http_{response.status_code}"
    except TimeoutError:
        result["error"] = "timeout"
    except (httpx.HTTPError, ValueError) as exc:
        result["error"] = type(exc).__name__
    result["seconds"] = time.monotonic() - start
    return result


def parsed_result(case, result):
    answer, usage, content = None, None, None
    error = result["error"]
    response = result.get("response")
    if isinstance(response, dict):
        usage = response.get("usage")
    if error is None:
        try:
            choice = response["choices"][0]
            content = choice["message"]["content"]
            if choice["finish_reason"] != "stop":
                error = "finish_" + str(choice["finish_reason"])
            elif choice["message"].get("tool_calls") or choice["message"].get("refusal"):
                error = "unexpected_tool_or_refusal"
            else:
                answer = parse_completion(content, case)
                if answer is None:
                    error = "invalid_answer_protocol"
        except (KeyError, IndexError, TypeError):
            error = "invalid_response_shape"
    return answer, error, usage, content


def freeze(root):
    files = [
        root / n
        for n in (
            "pilot.json",
            "formal.json",
            "request-hashes.json",
            "data-manifest.json",
            "downloads.json",
        )
    ]
    files.extend(Path("eval").glob("public_qa_*.py"))
    files.extend([Path("docs/superpowers/plans/2026-10-19-public-qa-benchmark.md")])
    if (root / "qualification.json").exists():
        files.append(root / "qualification.json")
        files.extend(root.glob("adapter-probe-*.json"))
        files.extend((root / "pilot").glob("*/*.json"))
        files.append(root / "pilot/stage.json")
    if (root / "resume-state.json").exists():
        files.append(root / "resume-state.json")
    # Paths are relative to repository root; root is itself repository-relative.
    manifest = {str(p): file_hash(p) for p in files}
    if (root / "resume-state.json").exists():
        prior = json.loads((root / "resume-state.json").read_text())["prior_results"]
        verify_frozen(Path("."), prior)
        manifest.update(prior)
    for item in json.loads((root / "data-manifest.json").read_text())["source_files"]:
        manifest[str(root / item["file"])] = item["sha256"]
    save(
        root / "frozen.json",
        {
            "files": manifest,
            "models": MODELS,
            "prices": PRICES,
            "endpoint": ENDPOINT,
            "max_tokens": 512,
            "temperature": 0,
            "concurrency": 5,
            "deadline": 60,
        },
    )


async def execute(root, secret_file, phase, resume=False):
    frozen = json.loads((root / "frozen.json").read_text())
    verify_frozen(Path("."), frozen["files"])
    if frozen["models"] != list(MODELS):
        raise ValueError("model configuration changed")
    cases = json.loads((root / f"{phase}.json").read_text())
    phase_root = root / phase
    if phase_root.exists() and not resume:
        raise ValueError("phase exists; refuse silent resume or overwrite")
    if not resume:
        phase_root.mkdir(mode=0o700)
    key = secret_file.read_text().strip()
    if not key or secret_file.stat().st_mode & 0o077:
        raise ValueError("credential file must be nonempty and private")
    expected = json.loads((root / "request-hashes.json").read_text())
    budget = Budget()
    allowed = list(MODELS)
    # Formal budget also accounts for the already executed pilot.
    if phase == "formal":
        qualification = json.loads((root / "qualification.json").read_text())
        allowed = qualification["eligible_models"]
        for p in sorted((root / "pilot").glob("*/*.json")):
            row = json.loads(p.read_text())
            budget.add(row["model"], row.get("usage"))
        for p in sorted(root.glob("adapter-probe-*.json")):
            row = json.loads(p.read_text())
            budget.add(row["model"], row.get("usage"))
    rows, stops, start = [], {}, time.monotonic()
    if resume:
        for path in sorted(phase_root.glob("*/*.json")):
            row = json.loads(path.read_text())
            if fingerprint(row["request"]) != expected[row["model"]][row["id"]]:
                raise ValueError("prior request changed")
            budget.add(row["model"], row.get("usage"))
            rows.append(
                {
                    k: v
                    for k, v in row.items()
                    if k not in {"request", "response", "response_text", "content"}
                }
            )
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60),
        follow_redirects=False,
        trust_env=False,
        limits=httpx.Limits(max_connections=5),
    ) as client:

        async def worker(model):
            if model not in allowed:
                stops[model] = "preflight_unusable_after_five_adapter_attempts"
                return
            folder = phase_root / model
            folder.mkdir(mode=0o700, exist_ok=resume)
            transport_failures = unusable = 0
            remaining = {c["id"] for c in remaining_cases(cases, rows, model)}
            for index, case in enumerate(cases):
                if case["id"] not in remaining:
                    continue
                reason = budget.reason()
                if reason:
                    stops[model] = reason
                    break
                payload = request_for(model, case)
                if fingerprint(payload) != expected[model][case["id"]]:
                    raise ValueError("payload changed after freezing")
                result = await bounded_call(client, ENDPOINT, payload, key)
                answer, error, usage, content = parsed_result(case, result)
                budget.add(model, usage)
                row = {
                    "id": case["id"],
                    "model": model,
                    "index": index,
                    "request": payload,
                    "request_sha256": fingerprint(payload),
                    **result,
                    "error": error,
                    "usage": usage,
                    "answer": answer,
                    "content": content,
                    "scores": score_case(case, answer),
                }
                path = folder / f"{index:04d}.json"
                if path.exists():
                    raise ValueError("refuse to overwrite existing raw response")
                save(path, row)
                rows.append(
                    {
                        k: v
                        for k, v in row.items()
                        if k not in {"request", "response", "response_text", "content"}
                    }
                )
                unusable = unusable + 1 if error else 0
                transport_failures = (
                    transport_failures + 1
                    if error in {"timeout", "ReadTimeout", "ConnectError"}
                    else 0
                )
                if result["http_status"] in {401, 403, 404}:
                    stops[model] = "provider_access_failure"
                    break
                if transport_failures >= 2 or unusable >= 5:
                    stops[model] = "consecutive_failure_limit"
                    break
                if (index + 1) % 25 == 0 or index + 1 == len(cases):
                    save(
                        phase_root / "progress.json",
                        {
                            "completed": len(rows),
                            "planned": len(cases) * len(MODELS),
                            "budget": vars(budget),
                            "elapsed_seconds": time.monotonic() - start,
                            "stops": stops,
                        },
                    )
                    print(
                        json.dumps(
                            {
                                "model": model,
                                "completed": index + 1,
                                "total_completed": len(rows),
                                "cost_rmb": budget.cost,
                            }
                        ),
                        flush=True,
                    )

        await asyncio.gather(*(worker(model) for model in MODELS))
    save(phase_root / "rows.json", rows)
    save(phase_root / "summary.json", aggregate(cases, rows, MODELS))
    save(
        phase_root / "stage.json",
        {
            "completed": len(rows),
            "planned": len(cases) * len(MODELS),
            "budget": vars(budget),
            "elapsed_seconds": time.monotonic() - start,
            "stops": stops,
            "qualified_kg_extraction": False,
            "full_pdf_built": False,
        },
    )
    print(json.dumps({"completed": len(rows), "budget": vars(budget), "stops": stops}), flush=True)


def report(root, phase):
    frozen = json.loads((root / "frozen.json").read_text())
    verify_frozen(Path("."), frozen["files"])
    cases = json.loads((root / f"{phase}.json").read_text())
    rows = []
    for p in sorted((root / phase).glob("*/*.json")):
        row = json.loads(p.read_text())
        if fingerprint(row["request"]) != row["request_sha256"]:
            raise ValueError("request ledger changed")
        case = next(c for c in cases if c["id"] == row["id"])
        raw_result = dict(row)
        if row["http_status"] == 200 and isinstance(row.get("response"), dict):
            raw_result["error"] = None
        answer, error, usage, _ = parsed_result(case, raw_result)
        if error != row["error"] or answer != row["answer"] or usage != row["usage"]:
            raise ValueError("parsed result changed")
        if score_case(case, answer) != row["scores"]:
            raise ValueError("score changed")
        rows.append(row)
    summary = aggregate(cases, rows, MODELS)
    save(root / phase / "verified-summary.json", summary)
    print(
        json.dumps(
            {m: {k: v for k, v in s.items() if k != "groups"} for m, s in summary.items()}, indent=2
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("freeze", "pilot", "formal", "report"))
    parser.add_argument("--root", type=Path, default=Path(".cache/public-qa-2026-10-19"))
    parser.add_argument("--secret-file", type=Path)
    parser.add_argument("--phase", choices=("pilot", "formal"), default="formal")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.command == "freeze":
        freeze(args.root)
    elif args.command == "report":
        report(args.root, args.phase)
    else:
        asyncio.run(execute(args.root, args.secret_file, args.command, resume=args.resume))
