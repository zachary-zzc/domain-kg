"""KG owners see scoped budget counters, never usage in other KGs or organizations."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from domain_kg.errors import DomainError
from domain_kg.identity.models import Action
from domain_kg.models import Namespace
from domain_kg.providers.budget_store import ProviderBudgets
from domain_kg.providers.usage_budget import BudgetBounds

pytestmark = pytest.mark.storage


async def test_usage_metrics_scope_pending_spend_and_current_grants(team):
    from domain_kg.operations.metrics import UsageMetrics

    _, policy, principals, members, org, kg = team
    store = ProviderBudgets(policy)
    metrics = UsageMetrics(policy, store)
    owner = await policy.resolve_access(principals["owner"], [kg], Action.CONFIGURE)
    ns = Namespace(org_id=org, kg_id=kg)
    initial = await metrics.get(owner, kg)
    assert initial.counts.spent_calls == initial.counts.reserved_calls == 0

    async def authorize():
        await policy.revalidate(owner, action=Action.CONFIGURE)

    reservation = await store.reserve(
        ns, uuid4(), BudgetBounds(input_tokens=100, output_tokens=20), authorize=authorize
    )
    held = await metrics.get(owner, kg)
    assert held.counts.reserved_calls == 1 and held.counts.reserved_input_tokens == 100
    await store.finish(reservation, outcome="reported", input_tokens=5, output_tokens=2)
    hidden_kg = uuid4()
    await policy.database.provider_budget_days.insert_one(
        {
            "org_id": str(org),
            "kg_id": str(hidden_kg),
            "scope": "kg",
            "day": datetime.now(UTC).date().isoformat(),
            "counts": {"spent_calls": 998},
        }
    )
    await policy.database.provider_budget_days.insert_one(
        {
            "org_id": str(uuid4()),
            "kg_id": str(kg),
            "scope": "kg",
            "day": datetime.now(UTC).date().isoformat(),
            "counts": {"spent_calls": 999},
        }
    )
    spent = await metrics.get(owner, kg)
    assert spent.counts.spent_calls == 1 and spent.counts.reserved_calls == 0
    assert spent.available.calls == store.limits.knowledge_base.calls - 1
    assert spent.available.input_tokens == store.limits.knowledge_base.input_tokens - 5
    assert spent.resets_at > spent.as_of and spent.resets_at.hour == 0
    assert spent.counts.spent_input_tokens == 5 and spent.counts.spent_output_tokens == 2
    assert str(hidden_kg) not in spent.model_dump_json()
    assert (
        "endpoint" not in spent.model_dump_json()
        and "organization_usage" not in spent.model_dump_json()
    )
    with pytest.raises(DomainError):
        await metrics.get(owner, hidden_kg)
    for role in ("editor", "reader", "denied"):
        actor = (
            await policy.resolve_access(principals[role], [kg], Action.READ)
            if role != "denied"
            else owner.model_copy(update={"principal": principals[role]})
        )
        with pytest.raises(DomainError):
            await metrics.get(actor, kg)
    await policy.database.members.update_one(
        {"org_id": str(org), "id": str(members["owner"].id)}, {"$set": {"suspended": True}}
    )
    with pytest.raises(DomainError):
        await metrics.get(owner, kg)


async def test_usage_metrics_rechecks_owner_after_counter_read(team, monkeypatch):
    from pymongo.asynchronous.collection import AsyncCollection

    from domain_kg.operations.metrics import UsageMetrics

    _, policy, principals, members, org, kg = team
    owner = await policy.resolve_access(principals["owner"], [kg], Action.CONFIGURE)
    read = AsyncCollection.find_one

    async def revoke_after_read(collection, *args, **kwargs):
        raw = await read(collection, *args, **kwargs)
        if collection.name == "provider_budget_days":
            await policy.database.members.update_one(
                {"org_id": str(org), "id": str(members["owner"].id)},
                {"$set": {"suspended": True}},
            )
        return raw

    monkeypatch.setattr(AsyncCollection, "find_one", revoke_after_read)
    with pytest.raises(DomainError):
        await UsageMetrics(policy, ProviderBudgets(policy)).get(owner, kg)
