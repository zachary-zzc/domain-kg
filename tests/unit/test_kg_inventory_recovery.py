import json

import pytest

from eval.kg_inventory_recovery import parse_inventory_check


def judgment(**changes):
    return {
        "id": "r1",
        "verdict": "supported",
        "matches": ["A uses B."],
        "reason": "Source says A uses B.",
        **changes,
    }


def test_inventory_check_keeps_verdict_and_reason_without_inventing_coverage():
    rows = [judgment(), judgment(id="r2", verdict="contradicted", reason="Direction reversed.")]
    result = parse_inventory_check(json.dumps({"facts": rows}), {"r1", "r2"})
    assert result == {"facts": [{**r, "matches": []} for r in rows]}


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [judgment(), judgment()],
        [judgment(id="wrong")],
        [judgment(verdict="maybe")],
        [judgment(matches="not a list")],
        [judgment(extra="unrecognized field")],
    ],
)
def test_inventory_recovery_still_rejects_incomplete_or_malformed_judgments(rows):
    with pytest.raises(ValueError):
        parse_inventory_check(json.dumps({"facts": rows}), {"r1"})
