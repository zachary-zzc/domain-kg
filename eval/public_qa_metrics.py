"""Independent local EM/F1 and support metrics, not an LLM judge.

SQuAD/Hotpot/MuSiQue use lower-case/article/punctuation token normalization.
DROP uses numeric-aware, order-independent multi-span matching. This local
metric preserves signed numeric values; it is not the upstream DROP scorer.
"""

import json
import re
import string
from collections import Counter
from decimal import Decimal, InvalidOperation


def normalized(text, numeric=False):
    text = text.lower().strip()
    if numeric:
        try:
            return str(Decimal(text.replace(",", "")).normalize())
        except InvalidOperation:
            pass
    text = text.translate(str.maketrans("", "", string.punctuation))
    return " ".join(re.sub(r"\b(a|an|the)\b", " ", text).split())


def token_f1(a, b):
    aa, bb = a.split(), b.split()
    if not aa or not bb:
        return float(aa == bb)
    overlap = sum((Counter(aa) & Counter(bb)).values())
    return 2 * overlap / (len(aa) + len(bb))


def span_scores(predicted, gold, numeric=False):
    p = [normalized(x, numeric) for x in predicted]
    g = [normalized(x, numeric) for x in gold]
    em = float(Counter(p) == Counter(g))
    if len(p) > 16 or len(g) > 16:
        return em, 0.0
    # Exact maximum one-to-one token-F1 assignment; missing spans count as zero.
    dp = {0: 0.0}
    for a in p:
        nxt = dict(dp)
        for mask, total in dp.items():
            for i, b in enumerate(g):
                if not mask & (1 << i):
                    score = token_f1(a, b)
                    if numeric:
                        nums_a = set(re.findall(r"(?<!\w)-?\d+(?:\.\d+)?", a))
                        nums_b = set(re.findall(r"(?<!\w)-?\d+(?:\.\d+)?", b))
                        if nums_b and not nums_b & nums_a:
                            score = 0
                    target = mask | (1 << i)
                    nxt[target] = max(nxt.get(target, 0), total + score)
        dp = nxt
    return em, max(dp.values(), default=0) / max(len(p), len(g), 1)


def parse_completion(raw, case):
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or set(data) != {"answer", "citations", "abstain"}:
        return None
    a, cites, abstain = data["answer"], data["citations"], data["abstain"]
    spans = [a] if isinstance(a, str) else a
    if (
        not isinstance(spans, list)
        or len(spans) > 16
        or not all(isinstance(x, str) and len(x) <= 2000 for x in spans)
        or type(abstain) is not bool
        or not isinstance(cites, list)
        or not all(isinstance(x, str) for x in cites)
        or len(cites) > 8
        or len(set(cites)) != len(cites)
        or not set(cites) <= {p["id"] for p in case["contexts"]}
    ):
        return None
    if abstain and (a != "" or cites):
        return None
    if not abstain and (not spans or any(not x.strip() for x in spans)):
        return None
    return data


def score_case(case, answer):
    zero = {
        "em": 0.0,
        "f1": 0.0,
        "citation_complete": 0.0,
        "citation_precision": 0.0,
        "citation_recall": 0.0,
        "protocol_ok": 0.0,
        "refusal_correct": 0.0,
    }
    if answer is None:
        return zero
    zero["protocol_ok"] = 1.0
    if not case["answerable"]:
        correct = float(answer["abstain"])
        return {**zero, "em": correct, "f1": correct, "refusal_correct": correct}
    if answer["abstain"]:
        return zero
    spans = [answer["answer"]] if isinstance(answer["answer"], str) else answer["answer"]
    scores = [span_scores(spans, gold, case["dataset"] == "drop") for gold in case["answers"]]
    em, f1 = max(s[0] for s in scores), max(s[1] for s in scores)
    gold, cited = set(case["support"]), set(answer["citations"])
    overlap = len(gold & cited)
    return {
        **zero,
        "em": em,
        "f1": f1,
        "citation_complete": float(gold <= cited),
        "citation_precision": overlap / len(cited) if cited else 0.0,
        "citation_recall": overlap / len(gold),
    }


def aggregate(cases, rows, models):
    expected = {c["id"]: c for c in cases}
    lookup = {}
    for r in rows:
        key = (r["model"], r["id"])
        if key in lookup or r["id"] not in expected or r["model"] not in models:
            raise ValueError("duplicate or unplanned result")
        lookup[key] = r
    result = {}
    for model in models:
        grouped = {"all": cases}
        for field in ("dataset", "difficulty", "stratum"):
            for value in {c[field] for c in cases}:
                grouped[f"{field}:{value}"] = [c for c in cases if c[field] == value]
        model_result = {}
        for group, members in grouped.items():
            rs = [lookup[(model, c["id"])] for c in members if (model, c["id"]) in lookup]
            n = len(members)
            answerable = sum(c["answerable"] for c in members)
            noanswer = n - answerable
            metrics = {
                "planned": n,
                "attempted": len(rs),
                "coverage": len(rs) / n,
                "errors": dict(Counter(r["error"] for r in rs if r.get("error"))),
            }
            for name in ("em", "f1", "protocol_ok"):
                metrics[name] = sum(r["scores"].get(name, 0) for r in rs) / n
            for name in ("citation_complete", "citation_precision", "citation_recall"):
                metrics[name] = (
                    sum(r["scores"].get(name, 0) for r in rs if expected[r["id"]]["answerable"])
                    / answerable
                    if answerable
                    else None
                )
            metrics["refusal_accuracy"] = (
                sum(r["scores"].get("refusal_correct", 0) for r in rs) / noanswer
                if noanswer
                else None
            )
            metrics["answerable_planned"] = answerable
            metrics["unanswerable_planned"] = noanswer
            model_result[group] = metrics
        result[model] = {**model_result["all"], "groups": model_result}
    return result
