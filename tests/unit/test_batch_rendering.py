"""A resumable batch renders a valid command and escapes source-derived labels."""

import json
from html.parser import HTMLParser
from uuid import UUID

from domain_kg.console.rendering import html
from domain_kg.operations.batch import BatchCommand, BatchItemResult, BatchResult, BatchTarget


def test_inline_resume_preserves_the_view_import_and_command_without_a_separate_page():
    identifier = UUID("00000000-0000-0000-0000-000000000001")
    command = BatchCommand(
        operation="retry", idempotency_key="resume-fixture", items=(BatchTarget(id=identifier),)
    )
    batch = BatchResult(
        org_id=identifier,
        kg_id=identifier,
        batch_id=identifier,
        operation="retry",
        command=command,
        items=(
            BatchItemResult(
                id=identifier, outcome="pending", title="<script>source label</script>"
            ),
        ),
    )
    response = html(
        "batch_notice.html",
        {
            "title": "Batch results",
            "batch": batch,
            "kg": {"id": identifier, "name": "Fixture"},
            "csrf": "fixture-token",
            "batch_counts": {
                "submitted": 0,
                "completed": 0,
                "skipped": 0,
                "failed": 0,
                "pending": 1,
            },
            "source_view": "attention",
            "import_id": identifier,
        },
    )

    class Fields(HTMLParser):
        def __init__(self):
            super().__init__()
            self.values = {}

        def handle_starttag(self, tag, attrs):
            data = dict(attrs)
            if tag == "input" and data.get("name"):
                self.values[data["name"]] = data.get("value", "")

    content = response.body.decode()
    fields = Fields()
    fields.feed(content)
    assert json.loads(fields.values["items"]) == [
        {"id": str(identifier), "revision": None, "version_id": None}
    ]
    assert fields.values["operation"] == "retry"
    assert fields.values["idempotency_key"] == "resume-fixture"
    assert fields.values["csrf_token"] == "fixture-token"
    assert fields.values["return_view"] == "attention"
    assert fields.values["import_id"] == str(identifier)
    assert 'id="batch-results"' in content
    assert "/batch/" not in content
    assert "<!doctype" not in content.lower()
    assert "<html" not in content.lower()
    assert "<script>source label</script>" not in content
    assert "&lt;script&gt;source label&lt;/script&gt;" in content


def test_inline_results_link_submitted_jobs_and_ready_restored_sources():
    identifier = UUID("00000000-0000-0000-0000-000000000001")
    job_id = UUID("00000000-0000-0000-0000-000000000002")
    source_id = UUID("00000000-0000-0000-0000-000000000003")
    command = BatchCommand(
        operation="restore",
        idempotency_key="restore-fixture",
        items=(BatchTarget(id=identifier, revision=1), BatchTarget(id=source_id, revision=2)),
    )
    batch = BatchResult(
        org_id=identifier,
        kg_id=identifier,
        batch_id=identifier,
        operation="restore",
        command=command,
        items=(
            BatchItemResult(id=identifier, outcome="submitted", title="Queued", job_id=job_id),
            BatchItemResult(id=source_id, outcome="completed", title="Available"),
        ),
    )
    content = html(
        "batch_notice.html",
        {
            "title": "Batch results",
            "kg": {"id": identifier, "name": "Fixture"},
            "batch": batch,
            "batch_counts": {
                "submitted": 1,
                "completed": 1,
                "skipped": 0,
                "failed": 0,
                "pending": 0,
            },
            "source_view": "removed",
        },
    ).body.decode()
    assert f"/admin/kgs/{identifier}/jobs/{job_id}" in content
    assert f"/admin/kgs/{identifier}/materials/{source_id}" in content
    assert 'data-outcome="submitted"' in content
    assert 'data-outcome="completed"' in content
