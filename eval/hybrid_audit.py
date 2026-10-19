"""Uniform postprocessing correction; preserves raw answers and original metrics."""

import copy
import re
from decimal import Decimal

from eval.hybrid_pilot import assess as original_assess
from eval.hybrid_pilot import normalized, summarize

NUMBER = re.compile(r"(?<![\w.])[+-]?\d+(?:\.\d+)?(?:e[+-]?\d+)?", re.I)


def numeric_marker(value):
    number = Decimal(value)
    if abs(number.adjusted()) > 12:
        return "number_outside_scoring_range"
    return "number_" + str(number.normalize()).casefold().replace(".", "_dot_").replace(
        "-", "minus_"
    )


def assess(question, selected, answer):
    numeric = {
        v for group in question["answers"] for v in group if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", v)
    }
    if numeric and answer:
        prepared = copy.deepcopy(question)
        prepared["answers"] = [
            [numeric_marker(v) if v in numeric else v for v in group]
            for group in question["answers"]
        ]
        response = {
            **answer,
            "answer": NUMBER.sub(lambda m: " " + numeric_marker(m[0]) + " ", answer["answer"]),
        }
        result = original_assess(prepared, selected, response)
    else:
        result = original_assess(question, selected, answer)
    if not question["expected"]:
        match = bool(answer and normalized(answer.get("answer", "")) == "insufficient_evidence")
        result["answer_match"] = match
        result["supported_correct"] = bool(
            match and result["citation_valid"] and not answer["citations"]
        )
    return result


def rescore(records, questions):
    questions = {q["id"]: q for q in questions}
    corrected = [
        {
            **row,
            "original_metrics": row["metrics"],
            "metrics": assess(questions[row["id"]], row["selected"], row.get("answer")),
        }
        for row in records
    ]
    return corrected, summarize(corrected)
