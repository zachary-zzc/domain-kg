"""Source-level ranking metrics; no-answer cases have no recall denominator."""

import math
from collections.abc import Sequence, Set


def score(
    returned: Sequence[str], expected: Set[str], *, top_k: int
) -> dict[str, float | bool | None]:
    if type(top_k) is not int or not 1 <= top_k <= 50:
        raise ValueError("top_k must be between 1 and 50")
    ranked = list(dict.fromkeys(returned))[:top_k]
    relevant = [value in expected for value in ranked]
    hits = sum(relevant)
    dcg = sum(1 / math.log2(rank + 2) for rank, hit in enumerate(relevant) if hit)
    ideal = sum(1 / math.log2(rank + 2) for rank in range(min(len(expected), top_k)))
    return {
        "recall": hits / len(expected) if expected else None,
        "precision": hits / len(ranked) if ranked else 0,
        "reciprocal_rank": next((1 / (i + 1) for i, hit in enumerate(relevant) if hit), 0),
        "ndcg": dcg / ideal if ideal else None,
        "false_positive": bool(ranked) and not expected,
    }
