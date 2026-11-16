"""The next import action and browser selection stay visible before long lists."""

from domain_kg.console.rendering import html


def test_import_has_a_named_picker_summary_and_actions_before_its_file_table():
    content = html(
        "import_form.html",
        {
            "title": "Sources",
            "kg": {"id": "fixture"},
            "import_run": None,
            "add_mode": "folder",
            "folder_copy": {},
            "source_view": "published",
            "command_key": "fixture",
        },
    ).body.decode()
    assert 'data-open-picker aria-describedby="picker-selection"' in content
    assert 'id="picker-selection" data-picker-selection' in content
    assert 'data-import-filter' in content and 'data-import-search' in content
    assert 'data-import-previous' in content and 'data-import-next' in content
    assert content.index("data-folder-start") < content.index("data-folder-rows")
    assert 'data-import-filter><option value="included"' in content
    assert 'data-folder-status role="status"' in content
