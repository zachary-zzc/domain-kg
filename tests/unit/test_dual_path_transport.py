import pytest

from eval.dual_path_run import EMBED_MODEL
from eval.dual_path_transport import TransportBudget, logical_rows


def test_embedding_unknown_reserve_uses_bounded_endpoint_capacity():
    budget = TransportBudget()
    budget.add(EMBED_MODEL, None)
    assert budget.reserved == pytest.approx(0.05)


def test_transport_retry_keeps_failed_attempt_and_uses_same_request():
    rows = [
        {
            "kind": "formal_graph",
            "model": "m",
            "label": "0",
            "identity": "a",
            "request_sha256": "h",
            "error": "http_429",
        },
        {
            "kind": "formal_graph",
            "model": "m",
            "label": "0#transport-1",
            "identity": "b",
            "request_sha256": "h",
            "error": None,
        },
    ]
    selected = logical_rows(rows)
    assert len(selected) == 1
    assert selected[0]["identity"] == "b"
    assert selected[0]["label"] == "0"
    assert selected[0]["attempt_identities"] == ["a", "b"]
    rows[1]["request_sha256"] = "changed"
    with pytest.raises(ValueError, match="retry changed request"):
        logical_rows(rows)
