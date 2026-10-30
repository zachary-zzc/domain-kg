"""Server-generated rejection counts survive chunk splitting and document publication."""

import re
from collections import Counter
from collections.abc import Iterable

REJECTION = re.compile(r"extraction_rejected_(entities|assertions):([1-9][0-9]{0,8})\Z")


def merge_warnings(warnings: Iterable[str]) -> tuple[str, ...]:
    counts: Counter[str] = Counter()
    other: set[str] = set()
    for warning in warnings:
        match = REJECTION.fullmatch(warning)
        if match:
            counts[match[1]] += int(match[2])
        else:
            other.add(warning)
    return (
        *sorted(other),
        *(
            f"extraction_rejected_{kind}:{counts[kind]}"
            for kind in ("entities", "assertions")
            if counts[kind]
        ),
    )
