"""Bounded UTC usage records never apply today's limits retroactively."""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Self
from uuid import UUID

from pydantic import Field, ValidationError, model_validator
from pymongo.errors import PyMongoError

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext, Action
from domain_kg.identity.policy import Policy
from domain_kg.models import FrozenModel, Namespace, UTCDateTime
from domain_kg.providers.usage_budget import BudgetCounts


class UsageWindow(FrozenModel):
    days: Annotated[int, Field(ge=1, le=31, strict=True)] = 7
    end_date: date | None = None

    @model_validator(mode="after")
    def past_window(self) -> Self:
        if self.end_date is not None and self.end_date > datetime.now(UTC).date():
            raise ValueError("usage history cannot select a future UTC day")
        if (self.end_date or datetime.now(UTC).date()).toordinal() < self.days:
            raise ValueError("usage history exceeds the calendar range")
        return self


class UsageDay(Namespace):
    day: date
    counts: BudgetCounts


class UsageHistory(Namespace):
    start_date: date
    end_date: date
    as_of: UTCDateTime
    items: Annotated[tuple[UsageDay, ...], Field(max_length=31)]

    @model_validator(mode="after")
    def exact_window(self) -> Self:
        dates = [row.day for row in self.items]
        if (
            not 0 <= (self.end_date - self.start_date).days < 31
            or self.end_date > self.as_of.date()
            or dates != sorted(set(dates), reverse=True)
            or any(
                (row.org_id, row.kg_id) != (self.org_id, self.kg_id)
                or not self.start_date <= row.day <= self.end_date
                for row in self.items
            )
        ):
            raise ValueError("usage days must be distinct, descending and inside their KG/window")
        return self


class UsageHistoryService:
    def __init__(self, policy: Policy) -> None:
        self.policy = policy

    async def get(
        self,
        access: AccessContext,
        kg: UUID,
        *,
        days: int = 7,
        end_date: date | None = None,
    ) -> UsageHistory:
        try:
            window = UsageWindow(days=days, end_date=end_date)
        except ValidationError:
            raise DomainError(
                ErrorCode.VALIDATION_ERROR, "Select a valid past UTC window of 1–31 days."
            ) from None
        current = await self.policy.revalidate(access, action=Action.CONFIGURE)
        if kg not in current.kg_ids:
            raise inaccessible_reference()
        timestamp = datetime.now(UTC)
        end = window.end_date or timestamp.date()
        start = end - timedelta(days=window.days - 1)
        try:
            rows = (
                await self.policy.database.provider_budget_days.find(
                    {
                        "org_id": str(current.org_id),
                        "kg_id": str(kg),
                        "scope": "kg",
                        "day": {"$gte": start.isoformat(), "$lte": end.isoformat()},
                    },
                    {"_id": 0, "org_id": 1, "kg_id": 1, "day": 1, "counts": 1},
                )
                .sort("day", -1)
                .limit(window.days + 1)
                .to_list()
            )
        except PyMongoError:
            raise DomainError(
                ErrorCode.BACKEND_UNAVAILABLE, "Usage history is unavailable.", retryable=True
            ) from None
        try:
            if len(rows) > window.days:
                raise ValueError("duplicate daily ledger")
            result = UsageHistory(
                org_id=current.org_id,
                kg_id=kg,
                start_date=start,
                end_date=end,
                as_of=timestamp,
                items=tuple(UsageDay.model_validate(row) for row in rows),
            )
        except (ValidationError, ValueError, KeyError, TypeError):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Usage history records are inconsistent."
            ) from None
        await self.policy.revalidate(current, action=Action.CONFIGURE)
        return result
