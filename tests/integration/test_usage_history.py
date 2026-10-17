"""Historical counters require a current owner and do not turn uncertainty into refunds."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.models import Namespace
from domain_kg.providers.budget_store import ProviderBudgets
from domain_kg.providers.usage_budget import BudgetBounds, BudgetCounts

pytestmark = pytest.mark.storage


async def test_history_exact_namespace_window_and_conservative_counters(team):
    from domain_kg.operations.usage_history import UsageHistoryService

    _, policy, principals, _, org, kg = team
    owner = await policy.resolve_access(principals["owner"], [kg], Action.CONFIGURE)
    service = UsageHistoryService(policy)
    assert (await service.get(owner, kg)).items == ()
    store = ProviderBudgets(policy)
    namespace = Namespace(org_id=org, kg_id=kg)

    async def authorize():
        await policy.revalidate(owner, action=Action.CONFIGURE)

    pending = await store.reserve(
        namespace, uuid4(), BudgetBounds(input_tokens=100, output_tokens=20), authorize=authorize
    )
    unknown = await store.reserve(
        namespace, uuid4(), BudgetBounds(input_tokens=80, output_tokens=10), authorize=authorize
    )
    await store.finish(unknown, outcome="unknown")
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    ns = {"org_id": str(org), "kg_id": str(kg), "scope": "kg"}
    await policy.database.provider_budget_days.insert_one(
        ns
        | {
            "day": yesterday.isoformat(),
            "counts": BudgetCounts(spent_calls=4, spent_input_tokens=321).model_dump(),
            "private_endpoint": "must-never-appear",
        }
    )
    for other in (
        {"kg_id": str(uuid4())},
        {"org_id": str(uuid4())},
        {"scope": "org", "kg_id": None},
    ):
        await policy.database.provider_budget_days.insert_one(
            ns
            | other
            | {"day": yesterday.isoformat(), "counts": BudgetCounts(spent_calls=999).model_dump()}
        )
    history = await service.get(owner, kg, days=3)
    assert len(history.items) == 2  # An absent day is not an invented zero row.
    assert history.items[0].counts == BudgetCounts(
        reserved_calls=1,
        reserved_input_tokens=100,
        reserved_output_tokens=20,
        spent_calls=1,
        spent_input_tokens=80,
        spent_output_tokens=10,
    )
    assert history.items[1].day == yesterday and history.items[1].counts.spent_input_tokens == 321
    assert (await service.get(owner, kg, days=1, end_date=yesterday)).items == (history.items[1],)
    assert (
        store.record(
            await policy.database.provider_budget_reservations.find_one({"id": str(pending.id)})
        ).state
        == "pending"
    )
    serialized = history.model_dump_json()
    assert "must-never-appear" not in serialized and "limits" not in serialized
    for days in (0, 32, True):
        with pytest.raises(DomainError) as invalid:
            await service.get(owner, kg, days=days)
        assert invalid.value.code == ErrorCode.VALIDATION_ERROR
    with pytest.raises(DomainError):
        await service.get(owner, uuid4())
    for role in ("reader", "editor"):
        actor = await policy.resolve_access(principals[role], [kg], Action.READ)
        with pytest.raises(DomainError):
            await service.get(actor, kg)
    await policy.database.provider_budget_days.update_one(
        ns | {"day": yesterday.isoformat()}, {"$set": {"counts.spent_calls": -1}}
    )
    with pytest.raises(DomainError) as corrupt:
        await service.get(owner, kg)
    assert corrupt.value.code == ErrorCode.PROJECTION_INCONSISTENT


async def test_history_revalidates_owner_after_cursor_read(team, monkeypatch):
    from pymongo.asynchronous.cursor import AsyncCursor

    from domain_kg.operations.usage_history import UsageHistoryService

    _, policy, principals, members, org, kg = team
    owner = await policy.resolve_access(principals["owner"], [kg], Action.CONFIGURE)
    read = AsyncCursor.to_list

    async def revoke(cursor, *args, **kwargs):
        rows = await read(cursor, *args, **kwargs)
        if cursor.collection.name == "provider_budget_days":
            await policy.database.members.update_one(
                {"org_id": str(org), "id": str(members["owner"].id)}, {"$set": {"suspended": True}}
            )
        return rows

    monkeypatch.setattr(AsyncCursor, "to_list", revoke)
    with pytest.raises(DomainError):
        await UsageHistoryService(policy).get(owner, kg)


async def test_history_backend_failure_returns_only_safe_error(team, monkeypatch):
    from pymongo.asynchronous.cursor import AsyncCursor
    from pymongo.errors import AutoReconnect

    from domain_kg.operations.usage_history import UsageHistoryService

    _, policy, principals, _, _, kg = team
    owner = await policy.resolve_access(principals["owner"], [kg], Action.CONFIGURE)
    read = AsyncCursor.to_list

    async def unavailable(cursor, *args, **kwargs):
        if cursor.collection.name == "provider_budget_days":
            raise AutoReconnect("fixture-private-endpoint")
        return await read(cursor, *args, **kwargs)

    monkeypatch.setattr(AsyncCursor, "to_list", unavailable)
    with pytest.raises(DomainError) as error:
        await UsageHistoryService(policy).get(owner, kg)
    assert error.value.code == ErrorCode.BACKEND_UNAVAILABLE and error.value.retryable
    assert "fixture-private-endpoint" not in str(error.value)
