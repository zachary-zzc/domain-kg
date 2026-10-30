"""Supplemental, answer/citation-independent analysis of unchanged raw responses.

Added after execution began to satisfy separate answer and citation reporting.
This is not the preregistered strict end-to-end score. No API calls, answer
rewrites, new aliases, LLM judging, regex JSON mining or truncated-output salvage.
"""

import json
import re
from collections import Counter
from pathlib import Path

from eval.public_qa_data import MODELS
from eval.public_qa_metrics import aggregate, span_scores
from eval.public_qa_prepare import file_hash, save
from eval.public_qa_run import verify_frozen


def independent_scores(case, row):
    out = {
        k: 0.0
        for k in (
            "answer_em",
            "answer_f1",
            "answer_parse_ok",
            "strict_json",
            "fence_normalized",
            "citation_protocol_ok",
            "citation_complete",
            "citation_precision",
            "citation_recall",
            "refusal_correct",
        )
    }
    if row.get("http_status") != 200:
        return out
    try:
        choice = row["response"]["choices"][0]
        if choice["finish_reason"] != "stop":
            return out
        message = choice["message"]
        if message.get("tool_calls") or message.get("refusal"):
            return out
        raw = message["content"].strip()
        try:
            data = json.loads(raw)
            out["strict_json"] = 1.0
        except ValueError:
            match = re.fullmatch(r"```(?:json)?\s*\n(.*)\n```", raw, flags=re.DOTALL)
            if not match:
                return out
            data = json.loads(match[1])
            out["fence_normalized"] = 1.0
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        return out
    if not isinstance(data, dict):
        return out
    cited = data.get("citations")
    valid_cites = (
        isinstance(cited, list)
        and len(cited) <= 8
        and all(isinstance(c, str) for c in cited)
        and len(set(cited)) == len(cited)
        and set(cited) <= {c["id"] for c in case["contexts"]}
    )
    if valid_cites:
        out["citation_protocol_ok"] = 1.0
        if case["answerable"]:
            gold, cited = set(case["support"]), set(cited)
            overlap = len(gold & cited)
            out["citation_complete"] = float(gold <= cited)
            out["citation_recall"] = overlap / len(gold)
            out["citation_precision"] = overlap / len(cited) if cited else 0.0
    answer, abstain = data.get("answer"), data.get("abstain")
    spans = [answer] if isinstance(answer, str) else answer
    if (
        type(abstain) is not bool
        or not isinstance(spans, list)
        or len(spans) > 16
        or not all(isinstance(s, str) and len(s) <= 2000 for s in spans)
        or (abstain and answer != "")
        or (not abstain and (not spans or any(not s.strip() for s in spans)))
    ):
        return out
    out["answer_parse_ok"] = 1.0
    if not case["answerable"]:
        correct = float(abstain)
        out.update(answer_em=correct, answer_f1=correct, refusal_correct=correct)
    elif not abstain:
        scores = [
            span_scores(spans, gold, numeric=case["dataset"] == "drop") for gold in case["answers"]
        ]
        out["answer_em"] = max(s[0] for s in scores)
        out["answer_f1"] = max(s[1] for s in scores)
    return out


def analyze(root):
    frozen = json.loads((root / "frozen.json").read_text())
    verify_frozen(Path("."), frozen["files"])
    cases = json.loads((root / "formal.json").read_text())
    lookup = {c["id"]: c for c in cases}
    raw_files = sorted((root / "formal").glob("*/*.json"))
    rows, hashes = [], {}
    for path in raw_files:
        r = json.loads(path.read_text())
        s = independent_scores(lookup[r["id"]], r)
        rows.append(
            {
                "id": r["id"],
                "model": r["model"],
                "error": r["error"],
                "scores": {
                    "em": s["answer_em"],
                    "f1": s["answer_f1"],
                    "protocol_ok": s["answer_parse_ok"],
                    **s,
                },
            }
        )
        hashes[str(path)] = file_hash(path)
    summary = aggregate(cases, rows, MODELS)
    extras = {}
    for model in MODELS:
        selected = [r for r in rows if r["model"] == model]
        extras[model] = {
            name: sum(r["scores"][name] for r in selected) / len(cases)
            for name in ("strict_json", "fence_normalized", "citation_protocol_ok")
        }
        extras[model]["paired_answer_correct_citation_incomplete"] = sum(
            r["scores"]["em"] == 1
            and r["scores"]["citation_complete"] == 0
            and lookup[r["id"]]["answerable"]
            for r in selected
        )
        extras[model]["error_counts"] = dict(Counter(r["error"] for r in selected if r["error"]))
    dest = root / "independent-analysis"
    dest.mkdir(exist_ok=True)
    save(dest / "rows.json", rows)
    save(dest / "summary.json", summary)
    save(dest / "extras.json", extras)
    save(
        dest / "manifest.json",
        {
            "definition": __doc__,
            "raw_files": hashes,
            "analysis_code_sha256": file_hash(Path(__file__)),
            "frozen_primary_sha256": file_hash(root / "frozen.json"),
            "planned": len(cases) * len(MODELS),
            "attempted": len(rows),
            "score_definitions_added_after_execution_started": True,
            "no_extra_calls": True,
        },
    )
    print(
        json.dumps(
            {m: {k: v for k, v in x.items() if k != "groups"} for m, x in summary.items()}, indent=2
        )
    )


if __name__ == "__main__":
    analyze(Path(".cache/public-qa-2026-10-19"))
