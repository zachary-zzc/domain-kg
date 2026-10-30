"""Daily organization/KG call budgets use one Mongo reservation boundary."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import Field
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.policy import Policy
from domain_kg.materials.repository import MaterialRepository
from domain_kg.models import Namespace, ScopedRef, UTCDateTime
from domain_kg.providers.usage_budget import (
    BudgetAmount,
    BudgetBounds,
    BudgetCounts,
    BudgetLimits,
    BudgetOutcome,
    reserve,
    settle,
)
from domain_kg.providers.usage_budget import BudgetPolicy as BudgetPolicy


class UsageReservation(ScopedRef):
    profile_id: UUID
    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    bounds: BudgetBounds
    state: Literal["pending", "reported", "not_attempted", "unknown"] = "pending"
    input_tokens: BudgetAmount = 0
    output_tokens: BudgetAmount = 0
    created_at: UTCDateTime


class ProviderBudgets:
    def __init__(self, policy: Policy, limits: BudgetPolicy | None = None) -> None:
        self.database, self.transactions = policy.database, MaterialRepository(policy)
        self.limits = limits or BudgetPolicy()

    async def initialize(self) -> None:
        await self.database.provider_budget_days.create_index(
            [("org_id", 1), ("kg_id", 1), ("scope", 1), ("day", 1)], unique=True
        )
        await self.database.provider_budget_reservations.create_index(
            [("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True
        )

    @staticmethod
    def invalid() -> DomainError:
        return DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Provider usage reservation differs.")

    @staticmethod
    def record(raw: dict[str, Any]) -> UsageReservation:
        values = {key: value for key, value in raw.items() if key != "_id"}
        timestamp = values.get("created_at")
        if isinstance(timestamp, datetime) and timestamp.tzinfo is None:
            values["created_at"] = timestamp.replace(tzinfo=UTC)
        return UsageReservation.model_validate(values)

    def buckets(
        self, namespace: Namespace, day: str
    ) -> tuple[tuple[dict[str, Any], BudgetLimits | None], ...]:
        common = {"org_id": str(namespace.org_id), "day": day}
        return (
            (common | {"scope": "org", "kg_id": None}, self.limits.organization),
            (common | {"scope": "kg", "kg_id": str(namespace.kg_id)}, self.limits.knowledge_base),
        )

    async def reserve(
        self,
        namespace: Namespace,
        profile_id: UUID,
        bounds: BudgetBounds,
        *,
        authorize: Callable[[], Awaitable[None]],
    ) -> UsageReservation:
        # The caller binds this guard to its verified source/query/restore
        # authority and exact approved provider namespace. A budget grants no access.
        await authorize()
        await self.initialize()
        created = datetime.now(UTC)
        reservation = UsageReservation(
            **namespace.model_dump(),
            id=uuid4(),
            profile_id=profile_id,
            day=created.date().isoformat(),
            bounds=bounds,
            created_at=created,
        )

        async def commit(session: AsyncClientSession) -> None:
            await authorize()
            for key, limit in self.buckets(namespace, reservation.day):
                await self.database.provider_budget_days.update_one(
                    key,
                    {"$setOnInsert": {"counts": BudgetCounts().model_dump()}},
                    upsert=True,
                    session=session,
                )
                raw = await self.database.provider_budget_days.find_one(key, session=session)
                if raw is None:
                    raise self.invalid()
                counts = reserve(BudgetCounts.model_validate(raw["counts"]), bounds, limit)
                await self.database.provider_budget_days.update_one(
                    key,
                    {"$set": {"counts": counts.model_dump()}},
                    session=session,
                )
            await self.database.provider_budget_reservations.insert_one(
                reservation.model_dump(mode="json") | {"created_at": created},
                session=session,
            )

        await self.transactions.transaction(commit)
        return reservation

    async def finish(
        self,
        reservation: UsageReservation,
        *,
        outcome: BudgetOutcome,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> UsageReservation:
        key = {name: str(getattr(reservation, name)) for name in ("org_id", "kg_id", "id")}
        namespace = Namespace(org_id=reservation.org_id, kg_id=reservation.kg_id)

        async def commit(session: AsyncClientSession) -> UsageReservation:
            raw = await self.database.provider_budget_reservations.find_one(key, session=session)
            if raw is None:
                raise self.invalid()
            current = self.record(raw)
            if current.model_dump(
                exclude={"state", "input_tokens", "output_tokens", "created_at"}
            ) != reservation.model_dump(
                exclude={"state", "input_tokens", "output_tokens", "created_at"}
            ):
                raise self.invalid()
            if current.state != "pending":
                if (current.state, current.input_tokens, current.output_tokens) != (
                    outcome,
                    input_tokens,
                    output_tokens,
                ):
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "Provider settlement differs."
                    )
                return current
            for bucket, _ in self.buckets(namespace, reservation.day):
                saved = await self.database.provider_budget_days.find_one(bucket, session=session)
                if saved is None:
                    raise self.invalid()
                counts = settle(
                    BudgetCounts.model_validate(saved["counts"]),
                    reservation.bounds,
                    outcome=outcome,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
                await self.database.provider_budget_days.update_one(
                    bucket,
                    {"$set": {"counts": counts.model_dump()}},
                    session=session,
                )
            result = current.model_copy(
                update={
                    "state": outcome,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                }
            )
            await self.database.provider_budget_reservations.update_one(
                key | {"state": "pending"},
                {
                    "$set": {
                        "state": outcome,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                    }
                },
                session=session,
            )
            return result

        return await self.transactions.transaction(commit)
