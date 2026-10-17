"""A provider call owns one durable budget hold, including uncertain outcomes."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.models import Namespace
from domain_kg.providers.budget_store import ProviderBudgets, UsageReservation
from domain_kg.providers.usage_budget import BudgetBounds

if TYPE_CHECKING:
    from domain_kg.providers.extraction import Usage


@dataclass
class BudgetCall:
    store: ProviderBudgets
    reservation: UsageReservation
    attempted: bool = False
    finished: bool = False

    async def report(self, usage: "Usage") -> None:
        if (usage.org_id, usage.kg_id, usage.profile_id) != (
            self.reservation.org_id,
            self.reservation.kg_id,
            self.reservation.profile_id,
        ):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Provider budget usage scope differs."
            )
        await self.store.finish(
            self.reservation,
            outcome="reported",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
        self.finished = True


class CallBudget:
    def __init__(
        self,
        store: ProviderBudgets,
        namespace: Namespace,
        profile_id: UUID,
        bounds: BudgetBounds,
        *,
        authorize: Callable[[], Awaitable[None]],
    ) -> None:
        self.store, self.namespace, self.profile_id = store, namespace, profile_id
        self.bounds, self.authorize = bounds, authorize

    @asynccontextmanager
    async def call(self, bounds: BudgetBounds | None = None) -> AsyncIterator[BudgetCall]:
        reservation = await self.store.reserve(
            self.namespace, self.profile_id, bounds or self.bounds, authorize=self.authorize
        )
        call = BudgetCall(self.store, reservation)
        try:
            # Permission/profile withdrawal between reservation and HTTP is a
            # proven unattempted call, so it returns the entire hold.
            await self.authorize()
            yield call
        finally:
            if not call.finished:
                await asyncio.shield(
                    self.store.finish(
                        reservation, outcome="unknown" if call.attempted else "not_attempted"
                    )
                )
