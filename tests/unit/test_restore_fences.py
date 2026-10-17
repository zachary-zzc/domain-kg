"""An older backup and current source fences combine without clearing either one."""


def test_current_exclusion_never_clears_a_captured_tombstone():
    from domain_kg.operations.restore_fences import material_fences

    assert material_fences(
        {"excluded": False, "tombstoned": True, "purged": False},
        {"excluded": True, "tombstoned": False, "purged": False},
    ) == {"excluded": True, "tombstoned": True, "purged": False}


def test_missing_or_purged_live_material_stays_withdrawn():
    from domain_kg.operations.restore_fences import material_fences

    captured = {"excluded": False, "tombstoned": False, "purged": False}
    assert material_fences(captured, None)["tombstoned"]
    assert material_fences(captured, {"excluded": False, "tombstoned": True, "purged": True}) == {
        "excluded": False,
        "tombstoned": True,
        "purged": True,
    }
    assert material_fences(captured, captured) == captured
