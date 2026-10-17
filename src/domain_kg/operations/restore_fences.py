"""Captured and current withdrawal fences combine monotonically during restore."""

from collections.abc import Mapping
from typing import Any

from domain_kg.errors import DomainError, ErrorCode


def material_fences(
    captured: Mapping[str, Any], current: Mapping[str, Any] | None
) -> dict[str, bool]:
    for row in (captured, current):
        if row is not None and any(
            type(row.get(key, False)) is not bool for key in ("excluded", "tombstoned", "purged")
        ):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Restore visibility flags are invalid."
            )
    live = current or {}
    purged = captured.get("purged", False) or live.get("purged", False)
    return {
        "excluded": captured.get("excluded", False) or live.get("excluded", False),
        "tombstoned": captured.get("tombstoned", False)
        or live.get("tombstoned", False)
        or purged
        or current is None,
        "purged": purged,
    }
