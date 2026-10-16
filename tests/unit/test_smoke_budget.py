"""A changed Compose stack must never bypass the user's hard resource ceiling."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "domain_kg_smoke_runner", Path(__file__).resolve().parents[2] / "scripts" / "smoke.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_memory_budget_allows_exact_ceiling() -> None:
    runner = load_runner()
    assert (
        runner.validate_memory_limits(
            [
                (500_000_000, 500_000_000),
                (500_000_000, 500_000_000),
                (400_000_000, 400_000_000),
                (100_000_000, 100_000_000),
            ]
        )
        == 1_500_000_000
    )


@pytest.mark.parametrize(
    "limits",
    [
        [(800_000_000, 800_000_000), (800_000_000, 800_000_000)],
        [(0, 0)],
        [(100_000_000, -1)],
        [(100_000_000, 200_000_000)],
    ],
)
def test_memory_budget_rejects_overcommit_unbounded_ram_and_swap(
    limits: list[tuple[int, int]],
) -> None:
    runner = load_runner()
    with pytest.raises(RuntimeError):
        runner.validate_memory_limits(limits)
