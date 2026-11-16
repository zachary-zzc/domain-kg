"""List controls page visible rows without changing cursor or filter boundaries."""

from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest

from domain_kg.console.rendering import html
from domain_kg.operations.batch import BatchCommand, BatchResult, BatchTarget


class PaginationDOM(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = {}
        self.forms = []
        self.current_form = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "a" and values.get("rel") in ("first", "next"):
            self.links[values["rel"]] = values["href"]
        if tag == "form" and "data-list-page-size" in values:
            self.current_form = {"attrs": values, "fields": {}, "options": []}
            self.forms.append(self.current_form)
        if self.current_form is not None:
            if tag == "input":
                self.current_form["fields"][values["name"]] = values["value"]
            if tag == "option":
                self.current_form["options"].append(values)

    def handle_endtag(self, tag):
        if tag == "form":
            self.current_form = None


@pytest.mark.parametrize("view", ["published", "processing", "attention", "removed"])
def test_pagination_preserves_view_filters_and_batch_but_page_size_restarts(view):
    page = SimpleNamespace(items=(object(),), next_cursor="opaque/next+token=")
    content = html(
        "list_pagination.html",
        {
            "title": "Sources",
            "listed": page,
            "listing_view": view,
            "cursor": "opaque-current",
            "limit": 10,
            "include_unverified": True,
            "batch": BatchResult(
                org_id=UUID(int=1),
                kg_id=UUID(int=1),
                batch_id=UUID(int=1),
                operation="retry",
                command=BatchCommand(
                    operation="retry",
                    items=(BatchTarget(id=UUID(int=1)),),
                    idempotency_key="pagination-fixture",
                ),
                items=(),
            ),
            "import_id": None,
        },
    ).body.decode()
    dom = PaginationDOM()
    dom.feed(content)
    expected = {
        "view": [view],
        "limit": ["10"],
        "include_unverified": ["true"],
        "batch_id": [str(UUID(int=1))],
    }
    assert parse_qs(urlsplit(dom.links["first"]).query) == expected
    assert parse_qs(urlsplit(dom.links["next"]).query) == expected | {
        "cursor": ["opaque/next+token="]
    }
    assert len(dom.forms) == 1
    form = dom.forms[0]
    assert form["attrs"]["method"] == "get"
    assert form["fields"] == {key: value[0] for key, value in expected.items() if key != "limit"}
    assert [option["value"] for option in form["options"]] == ["10", "25", "50"]
    assert "selected" in form["options"][0]
    assert "Rows shown" in content
    assert "opaque-current" not in content


def test_single_page_has_no_dead_navigation_or_invented_total():
    content = html(
        "list_pagination.html",
        {
            "title": "Task history",
            "listed": SimpleNamespace(items=(), next_cursor=None),
            "listing_view": "all",
            "cursor": None,
            "limit": 1,
        },
    ).body.decode()
    dom = PaginationDOM()
    dom.feed(content)
    assert dom.links == {}
    assert len(dom.forms) == 1
    options = dom.forms[0]["options"]
    assert options[0]["value"] == "1" and "selected" in options[0]
    assert "total" not in content.lower()
