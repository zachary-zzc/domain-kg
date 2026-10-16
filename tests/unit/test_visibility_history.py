"""Visibility history is an authority action, not a fabricated projection job."""

from datetime import UTC, datetime
from uuid import uuid4

from domain_kg.revisions.history_models import HistoryPage, VisibilityHistoryEntry


def test_visibility_history_has_truthful_state_without_a_fake_job_or_published_manifest():
    entry = VisibilityHistoryEntry(
        revision_id=uuid4(),
        target_id=uuid4(),
        target_revision=1,
        state="accepted",
        public=False,
        material_ids=(uuid4(),),
        provenance={"actor": "fixture-editor", "reason": "Suppress source scope"},
        created_at=datetime.now(UTC),
        visibility_revision=3,
        before={"suppressed": False},
        after={"suppressed": True},
    )
    assert entry.job_id is None and entry.published_revision is None
    page = HistoryPage.model_validate({"items": [entry.model_dump(mode="json")]})
    assert page.items[0].kind == "visibility" and page.items[0].after.suppressed
