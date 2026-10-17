"""HTTP cannot begin after a revoked reservation; uncertain failures keep the bound."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from domain_kg.errors import DomainError, inaccessible_reference
from domain_kg.models import Namespace
from domain_kg.providers.budget_store import UsageReservation
from domain_kg.providers.extraction import Usage
from domain_kg.providers.usage_budget import BudgetBounds


def meter_fixture():
    namespace = Namespace(org_id=uuid4(), kg_id=uuid4())
    reservation = UsageReservation(
        **namespace.model_dump(),
        id=uuid4(),
        profile_id=uuid4(),
        day="2026-10-17",
        bounds=BudgetBounds(input_tokens=10),
        created_at=datetime.now(UTC),
    )
    store = SimpleNamespace(reserve=AsyncMock(return_value=reservation), finish=AsyncMock())
    return namespace, reservation, store


async def test_meter_records_reported_usage_once_before_returning():
    from domain_kg.providers.call_budget import CallBudget

    ns, reservation, store = meter_fixture()
    meter = CallBudget(store, ns, reservation.profile_id, reservation.bounds, authorize=AsyncMock())
    async with meter.call() as call:
        store.reserve.assert_awaited_once()
        call.attempted = True
        await call.report(
            Usage(
                org_id=ns.org_id,
                kg_id=ns.kg_id,
                profile_id=reservation.profile_id,
                input_tokens=3,
                output_tokens=0,
            )
        )
    store.finish.assert_awaited_once_with(
        reservation, outcome="reported", input_tokens=3, output_tokens=0
    )


async def test_meter_refunds_unattempted_call_when_post_reservation_access_is_revoked():
    from domain_kg.providers.call_budget import CallBudget

    ns, reservation, store = meter_fixture()
    meter = CallBudget(
        store,
        ns,
        reservation.profile_id,
        reservation.bounds,
        authorize=AsyncMock(side_effect=inaccessible_reference()),
    )
    with pytest.raises(DomainError):
        async with meter.call():
            pytest.fail("revocation must prevent the HTTP body from running")
    store.finish.assert_awaited_once_with(reservation, outcome="not_attempted")


async def test_meter_conservatively_charges_an_attempted_call_without_known_usage():
    from domain_kg.providers.call_budget import CallBudget

    ns, reservation, store = meter_fixture()
    meter = CallBudget(store, ns, reservation.profile_id, reservation.bounds, authorize=AsyncMock())
    with pytest.raises(RuntimeError):
        async with meter.call() as call:
            call.attempted = True
            raise RuntimeError("synthetic interrupted response")
    store.finish.assert_awaited_once_with(reservation, outcome="unknown")
