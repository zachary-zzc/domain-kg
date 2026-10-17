"""History windows and returned counts stay bounded and explicitly scoped."""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_kg.providers.usage_budget import BudgetCounts


def test_usage_window_validates_real_utc_dates_and_small_bound():
    from domain_kg.operations.usage_history import UsageWindow

    assert UsageWindow(days=7, end_date=date(2026, 1, 31)).days == 7
    for values in (
        {"days": 32},
        {"days": 0},
        {"days": True},
        {"end_date": "2026-02-30"},
        {"end_date": "0001-01-01", "days": 31},
        {"end_date": datetime.now(UTC).date() + timedelta(days=1)},
    ):
        with pytest.raises(ValidationError):
            UsageWindow.model_validate(values)


def test_usage_history_rejects_duplicate_outside_window_and_foreign_days():
    from domain_kg.operations.usage_history import UsageDay, UsageHistory

    org, kg = uuid4(), uuid4()
    row = UsageDay(org_id=org, kg_id=kg, day=date(2026, 1, 30), counts=BudgetCounts())
    values = dict(
        org_id=org,
        kg_id=kg,
        start_date=date(2026, 1, 25),
        end_date=date(2026, 1, 31),
        as_of=datetime.now(UTC),
        items=(row,),
    )
    assert UsageHistory(**values).items == (row,)
    for items in (
        (row, row),
        (row.model_copy(update={"kg_id": uuid4()}),),
        (row.model_copy(update={"day": date(2026, 1, 1)}),),
    ):
        with pytest.raises(ValidationError):
            UsageHistory.model_validate(values | {"items": items})
