"""Source rows keep readable filenames and escaped, complete selection identities."""

from datetime import UTC, datetime
from html.parser import HTMLParser
from types import SimpleNamespace
from uuid import UUID

import pytest

from domain_kg.console.rendering import html
from domain_kg.jobs.models import JobReceipt
from domain_kg.materials.import_runs import ImportRun, ImportRunItem


class SourceRows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_source = False
        self.capture = None
        self.names = []
        self.parents = []
        self.path_titles = []
        self.selections = []
        self.source_links = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "td" and values.get("class") == "source-name":
            self.in_source = True
        if self.in_source and tag == "a":
            self.capture = self.names
            self.source_links.append(values["href"])
        elif self.in_source and tag == "strong" and self.capture is None:
            self.capture = self.names
        elif self.in_source and tag == "small" and values.get("class") == "source-path":
            self.capture = self.parents
            self.path_titles.append(values["title"])
        if tag == "input" and "data-bulk-item" in values:
            self.selections.append(values)

    def handle_data(self, data):
        if self.capture is not None:
            self.capture.append(data)

    def handle_endtag(self, tag):
        if tag in ("a", "small", "strong"):
            self.capture = None
        if tag == "td":
            self.in_source = False


def render_source(title, *, editable=True):
    identifier = UUID("00000000-0000-0000-0000-000000000001")
    version = UUID("00000000-0000-0000-0000-000000000002")
    content = html(
        "materials.html",
        {
            "title": "Sources",
            "kg": {"id": identifier, "name": "Synthetic KG"},
            "can_edit": editable,
            "source_view": "published",
            "batch": None,
            "import_id": None,
            "import_run": None,
            "import_runs": (),
            "add_mode": None,
            "include_unverified": False,
            "bulk_scope": "#material-list",
            "bulk_kind": "materials",
            "command_key": "source-list-fixture",
            "materials": SimpleNamespace(
                items=(
                    SimpleNamespace(
                        id=identifier,
                        title=title,
                        revision=7,
                        active_version_id=version,
                        import_origin=None,
                    ),
                ),
                next_cursor=None,
            ),
        },
    ).body.decode()
    rows = SourceRows()
    rows.feed(content)
    return content, rows


@pytest.mark.parametrize(
    ("title", "filename", "parent"),
    [
        ("Research/资料/Alpha & Beta.pdf", "Alpha & Beta.pdf", "Research/资料"),
        (
            'Team <img src=x onerror=alert(1)>/Nested & Notes/"<unsafe>.pdf"',
            '"<unsafe>.pdf"',
            "Team <img src=x onerror=alert(1)>/Nested & Notes",
        ),
    ],
)
def test_published_source_shows_filename_and_parent_but_selects_the_complete_title(
    title, filename, parent
):
    content, rows = render_source(title)
    assert rows.names == [filename]
    assert rows.parents == [parent]
    assert rows.path_titles == [title]
    assert len(rows.selections) == 1
    selection = rows.selections[0]
    assert selection["data-bulk-label"] == title
    assert selection["aria-label"] == f"Select {title}"
    assert selection["data-bulk-id"] == "00000000-0000-0000-0000-000000000001"
    assert selection["data-bulk-revision"] == "7"
    assert selection["data-bulk-version"] == "00000000-0000-0000-0000-000000000002"
    assert rows.source_links == [
        "/admin/kgs/00000000-0000-0000-0000-000000000001/materials/"
        "00000000-0000-0000-0000-000000000001?include_unverified=false"
    ]
    assert "<img src=x" not in content
    assert "<unsafe>" not in content
    assert "&amp;" in content


def test_single_filename_has_no_parent_row_or_reader_selection_controls():
    content, rows = render_source("单独 & document.txt", editable=False)
    assert rows.names == ["单独 & document.txt"]
    assert rows.parents == []
    assert rows.path_titles == []
    assert rows.selections == []
    assert "bulk-management" not in content


@pytest.mark.parametrize("template", ["job_table.html", "removed_sources.html"])
def test_processing_and_removed_rows_use_the_same_filename_and_parent_hierarchy(template):
    identifier = UUID(int=1)
    title = "Nested <unsafe>/References/Source & evidence.pdf"
    source = SimpleNamespace(title=title, material_id=identifier)
    job = SimpleNamespace(
        source=source,
        job_id=identifier,
        kind="ingest",
        created_at=None,
        stage="queued",
        progress=0,
        error=None,
    )
    removed = SimpleNamespace(
        title=title,
        id=identifier,
        revision=7,
        state="excluded",
        retention_until=None,
        can_restore=True,
    )
    content = html(
        template,
        {
            "title": "Sources",
            "kg": {"id": identifier},
            "can_edit": True,
            "bulk_kind": "jobs",
            "jobs": SimpleNamespace(items=(job,)),
            "managed": SimpleNamespace(items=(removed,), next_cursor=None),
            "source_view": "removed",
            "command_key": "source-row-fixture",
        },
    ).body.decode()
    rows = SourceRows()
    rows.feed(content)
    assert rows.names == ["Source & evidence.pdf"]
    assert rows.parents == ["Nested <unsafe>/References"]
    assert rows.path_titles == [title]
    assert rows.selections[0]["data-bulk-label"] == title
    assert "<unsafe>" not in content


class ImportStatusDOM(HTMLParser):
    VOID = frozenset(
        {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }
    )

    def __init__(self):
        super().__init__()
        self.stack = []
        self.statuses = []
        self.details = {}
        self.current_import = None
        self.nodes = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        self.nodes.append((tag, values, tuple(self.stack)))
        if "data-folder-status" in values:
            self.statuses.append((values, tuple(self.stack)))
        if tag == "details":
            self.details[values.get("id")] = values
        if "data-folder-current" in values:
            self.current_import = values
        if tag not in self.VOID:
            self.stack.append((tag, values))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                return


@pytest.mark.parametrize("stage", ["queued", "ready"])
def test_saved_import_status_remains_visible_when_the_upload_form_is_collapsed(stage):
    identifier = UUID("00000000-0000-0000-0000-000000000001")
    run = ImportRun(
        id=identifier,
        org_id=identifier,
        kg_id=identifier,
        created_at=datetime(2026, 10, 31, tzinfo=UTC),
        items=(
            ImportRunItem(
                index=0,
                path="Examples/source.txt",
                sha256="a" * 64,
                size_bytes=12,
                media_type="text/plain",
                state="submitted",
                job=JobReceipt(org_id=identifier, kg_id=identifier, job_id=identifier, stage=stage),
            ),
        ),
    )
    content = html(
        "import_form.html",
        {
            "title": "Sources",
            "kg": {"id": identifier},
            "import_run": run,
            "add_mode": None,
            "folder_copy": {},
            "source_view": "processing",
            "command_key": "saved-import-fixture",
        },
    ).body.decode()
    dom = ImportStatusDOM()
    dom.feed(content)
    assert "open" not in dom.details["import-input"]
    assert dom.current_import is not None and "hidden" not in dom.current_import
    assert len(dom.statuses) == 1
    attributes, ancestors = dom.statuses[0]
    assert attributes["role"] == "status"
    assert attributes["aria-live"] == "polite"
    assert "hidden" not in attributes
    assert not any(tag == "details" or "hidden" in values for tag, values in ancestors)
    assert "Already uploaded files continue in the background." in content


def test_batch_review_uses_a_named_modal_with_safe_cancel_and_explicit_confirmation():
    content, _ = render_source("Example/source.pdf")
    dom = ImportStatusDOM()
    dom.feed(content)
    dialogs = [(tag, attrs) for tag, attrs, _ in dom.nodes if "data-bulk-dialog" in attrs]
    assert len(dialogs) == 1
    tag, attrs = dialogs[0]
    assert tag == "dialog" and "open" not in attrs
    assert attrs["aria-labelledby"] == "bulk-review-title"
    assert attrs["aria-describedby"] == "bulk-review-message"
    for selector in ("data-bulk-confirm", "data-bulk-cancel"):
        buttons = [
            (tag, attrs, ancestors) for tag, attrs, ancestors in dom.nodes if selector in attrs
        ]
        assert len(buttons) == 1
        tag, attrs, ancestors = buttons[0]
        assert tag == "button" and attrs["type"] == "button"
        assert any(tag == "dialog" for tag, _ in ancestors)
        if selector == "data-bulk-cancel":
            assert "autofocus" in attrs
    form = next(attrs for tag, attrs, _ in dom.nodes if "data-bulk-form" in attrs)
    assert form["method"] == "post"
    assert form["action"].endswith("/batch")
    assert "provider charges" in form["data-confirm-process"]
