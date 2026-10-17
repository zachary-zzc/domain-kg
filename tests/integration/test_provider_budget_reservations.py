"""Real scoped reservations precede mocked HTTP and survive races and uncertain calls."""
# ruff: noqa: F811

import asyncio
import json

import httpx
import pytest
from test_publication import publishing  # noqa: F401

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.models import Namespace
from domain_kg.providers.budget_store import BudgetPolicy, ProviderBudgets, UsageReservation
from domain_kg.providers.usage_budget import BudgetBounds, BudgetLimits

pytestmark = pytest.mark.storage


def limited_store(queue):
    limit = BudgetLimits(input_tokens=128000, output_tokens=0, calls=1)
    return ProviderBudgets(queue.policy, BudgetPolicy(organization=limit, knowledge_base=limit))


@pytest.mark.parametrize("outage", [False, True])
async def test_budget_exists_before_http_and_exhaustion_sends_no_second_request(
    publishing, team, outage
):
    _, source_access, queue, coordinator, _, _ = publishing
    kg = source_access.kg_ids[0]
    ns = Namespace(org_id=team[4], kg_id=kg)
    scope = ns.model_dump(mode="json")
    coordinator.providers.budgets = limited_store(queue)
    calls = []

    async def respond(request):
        pending = await queue.database.provider_budget_reservations.find_one(
            scope | {"state": "pending"}
        )
        assert pending is not None, "provider HTTP must follow a durable reservation"
        calls.append(1)
        if outage:
            return httpx.Response(503)
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": payload["model"],
                "data": [{"index": 0, "embedding": [1.0, 0.0, 0.0]}],
                "usage": {"prompt_tokens": 5},
            },
        )

    coordinator.providers.transport = httpx.MockTransport(respond)
    access = await queue.policy.resolve_access(team[2]["owner"], [kg], Action.SEARCH)
    if outage:
        with pytest.raises(DomainError):
            await coordinator.providers.embeddings(access, ns, ["Alpha"])
    else:
        _, vectors = await coordinator.providers.embeddings(access, ns, ["Alpha"])
        assert len(vectors) == 1
    assert len(calls) == 1
    with pytest.raises(DomainError) as exhausted:
        await coordinator.providers.embeddings(access, ns, ["Beta"])
    assert exhausted.value.code == ErrorCode.QUOTA_EXCEEDED and len(calls) == 1
    bucket = await queue.database.provider_budget_days.find_one(scope | {"scope": "kg"})
    assert bucket["counts"]["reserved_calls"] == 0
    assert bucket["counts"]["spent_calls"] == 1
    profile = next(iter(coordinator.providers.registry.profiles.values()))
    assert bucket["counts"]["spent_input_tokens"] == (
        min(128000, profile.embedding_context_tokens) if outage else 5
    )


async def test_budget_revocation_before_http_refunds_only_unattempted_call(
    publishing, team, monkeypatch
):
    _, source_access, queue, coordinator, _, _ = publishing
    ns = Namespace(org_id=source_access.org_id, kg_id=source_access.kg_ids[0])
    store = limited_store(queue)
    coordinator.providers.budgets = store
    original = store.reserve

    async def reserve_then_revoke(*args, **kwargs):
        result = await original(*args, **kwargs)
        await queue.database.kg_grants.delete_one(
            {
                "org_id": str(ns.org_id),
                "kg_id": str(ns.kg_id),
                "subject_id": str(team[3]["reader"].id),
            }
        )
        await queue.database.organizations.update_one(
            {"_id": str(ns.org_id)}, {"$inc": {"policy_revision": 1}}
        )
        return result

    async def forbidden(request):
        pytest.fail("revoked caller must never send provider HTTP")

    monkeypatch.setattr(store, "reserve", reserve_then_revoke)
    coordinator.providers.transport = httpx.MockTransport(forbidden)
    access = await queue.policy.resolve_access(team[2]["reader"], [ns.kg_id], Action.SEARCH)
    with pytest.raises(DomainError):
        await coordinator.providers.embeddings(access, ns, ["Alpha"])
    row = await queue.database.provider_budget_reservations.find_one(ns.model_dump(mode="json"))
    assert row is not None and row["state"] == "not_attempted"
    bucket = await queue.database.provider_budget_days.find_one(
        ns.model_dump(mode="json") | {"scope": "kg"}
    )
    assert bucket["counts"]["reserved_calls"] == bucket["counts"]["spent_calls"] == 0


async def test_budget_race_and_settlement_replay_preserve_both_scoped_ledgers(publishing, team):
    _, source_access, queue, coordinator, _, _ = publishing
    ns = Namespace(org_id=source_access.org_id, kg_id=source_access.kg_ids[0])
    limit = BudgetLimits(input_tokens=10, output_tokens=0, calls=2)
    store = ProviderBudgets(queue.policy, BudgetPolicy(organization=limit, knowledge_base=limit))
    profile = next(iter(coordinator.providers.registry.profiles.values()))
    access = await queue.policy.resolve_access(team[2]["owner"], [ns.kg_id], Action.SEARCH)

    async def authorize():
        await queue.policy.revalidate(access, action=Action.SEARCH)

    results = await asyncio.gather(
        *(
            store.reserve(ns, profile.id, BudgetBounds(input_tokens=6), authorize=authorize)
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, UsageReservation) for result in results) == 1
    assert (
        sum(
            isinstance(result, DomainError) and result.code == ErrorCode.QUOTA_EXCEEDED
            for result in results
        )
        == 1
    )
    winner = next(result for result in results if isinstance(result, UsageReservation))
    await store.finish(winner, outcome="reported", input_tokens=2)
    await store.finish(winner, outcome="reported", input_tokens=2)
    rows = await queue.database.provider_budget_days.find({"org_id": str(ns.org_id)}).to_list()
    assert len(rows) == 2
    assert all(
        row["counts"]["spent_input_tokens"] == 2 and row["counts"]["reserved_input_tokens"] == 0
        for row in rows
    )


async def test_extraction_budget_is_checked_before_sending_source_text(publishing, monkeypatch):
    from test_publication import prepare

    _, _, queue, coordinator, _, _ = publishing
    disabled = BudgetLimits(input_tokens=0, output_tokens=0, calls=0)
    coordinator.providers.budgets = ProviderBudgets(
        queue.policy, BudgetPolicy(organization=disabled, knowledge_base=disabled)
    )

    async def forbidden(request):
        pytest.fail("exhausted extraction budget must not send source text")

    monkeypatch.setattr(coordinator.providers, "transport", httpx.MockTransport(forbidden))
    with pytest.raises(DomainError) as denied:
        await prepare(publishing, key="budget-before-extraction")
    assert denied.value.code == ErrorCode.QUOTA_EXCEEDED
