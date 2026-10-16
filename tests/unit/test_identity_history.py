"""Identity history reports partitions and compensation, never permission snapshots."""

from datetime import UTC, datetime
from uuid import uuid4

from domain_kg.revisions.history_models import HistoryPage


def test_identity_history_and_control_have_explicit_typed_states():
    alpha, beta, revision = uuid4(), uuid4(), uuid4()
    common = {
        "revision_id": revision,
        "target_id": alpha,
        "target_revision": 1,
        "state": "accepted",
        "public": True,
        "material_ids": (),
        "provenance": {"actor": "fixture-owner", "reason": "Reviewed grouping"},
        "created_at": datetime.now(UTC),
        "job_id": uuid4(),
    }
    page = HistoryPage.model_validate(
        {
            "items": (
                common
                | {
                    "kind": "identity",
                    "operation": "merge",
                    "member_ids": (alpha, beta),
                    "before": {"groups": ()},
                    "after": {"groups": ({"canonical_id": alpha, "member_ids": (alpha, beta)},)},
                },
                common
                | {
                    "revision_id": uuid4(),
                    "kind": "identity_control",
                    "member_ids": (alpha, beta),
                    "before": {"enabled": True},
                    "after": {"enabled": False},
                    "original_revision_id": revision,
                    "rollback_of": revision,
                },
            )
        }
    )
    assert len(page.items) == 2
    assert page.items[0].kind == "identity" and page.items[1].kind == "identity_control"
