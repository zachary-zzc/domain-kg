"""Provider bounds must be reserved before a call and settled without double spending."""

import pytest

from domain_kg.errors import DomainError


def test_budget_reserves_full_call_bounds_and_rejects_concurrent_overcommit():
    from domain_kg.providers.usage_budget import BudgetBounds, BudgetCounts, BudgetLimits, reserve

    limit = BudgetLimits(input_tokens=10, output_tokens=5, calls=1)
    bounds = BudgetBounds(input_tokens=10, output_tokens=5)
    pending = reserve(BudgetCounts(), bounds, limit)
    assert pending.reserved_input_tokens == 10 and pending.reserved_calls == 1
    with pytest.raises(DomainError):
        reserve(pending, bounds, limit)


@pytest.mark.parametrize("outcome", ["reported", "not_attempted", "unknown"])
def test_budget_settlement_returns_only_proven_unused_capacity(outcome):
    from domain_kg.providers.usage_budget import (
        BudgetBounds,
        BudgetCounts,
        BudgetLimits,
        reserve,
        settle,
    )

    bounds = BudgetBounds(input_tokens=10, output_tokens=5)
    pending = reserve(
        BudgetCounts(), bounds, BudgetLimits(input_tokens=20, output_tokens=10, calls=2)
    )
    result = settle(
        pending,
        bounds,
        outcome=outcome,
        input_tokens=3 if outcome == "reported" else 0,
        output_tokens=2 if outcome == "reported" else 0,
    )
    assert (
        result.reserved_calls == result.reserved_input_tokens == result.reserved_output_tokens == 0
    )
    assert result.spent_input_tokens == (
        3 if outcome == "reported" else 10 if outcome == "unknown" else 0
    )
    assert result.spent_output_tokens == (
        2 if outcome == "reported" else 5 if outcome == "unknown" else 0
    )
    assert result.spent_calls == (0 if outcome == "not_attempted" else 1)
    with pytest.raises(DomainError):
        settle(result, bounds, outcome=outcome)


def test_budget_rejects_overreported_usage_instead_of_returning_unheld_capacity():
    from domain_kg.providers.usage_budget import (
        BudgetBounds,
        BudgetCounts,
        BudgetLimits,
        reserve,
        settle,
    )

    bounds = BudgetBounds(input_tokens=10, output_tokens=5)
    pending = reserve(
        BudgetCounts(), bounds, BudgetLimits(input_tokens=20, output_tokens=10, calls=2)
    )
    with pytest.raises(DomainError):
        settle(pending, bounds, outcome="reported", input_tokens=11)
