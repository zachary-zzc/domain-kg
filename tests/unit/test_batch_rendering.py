"""A resumable batch renders a valid command and escapes source-derived labels."""

import json
from html.parser import HTMLParser
from uuid import UUID

from domain_kg.console.rendering import html
from domain_kg.operations.batch import BatchCommand, BatchItemResult, BatchResult, BatchTarget


def test_resume_form_preserves_the_item_list_and_escapes_source_labels():
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
        "batch_result.html",
        {
            "title": "Batch results",
            "batch": batch,
            "kg": {"id": identifier, "name": "Fixture"},
            "csrf": "fixture-token",
            "counts": {"submitted": 0, "completed": 0, "skipped": 0, "failed": 0, "pending": 1},
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
    assert "<script>source label</script>" not in content
    assert "&lt;script&gt;source label&lt;/script&gt;" in content
