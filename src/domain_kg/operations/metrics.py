"""Current-owner daily KG budget views omit other KGs and organization aggregates."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import Field, ValidationError

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext, Action
from domain_kg.identity.policy import Policy
from domain_kg.models import Namespace, UTCDateTime
from domain_kg.providers.budget_store import ProviderBudgets
from domain_kg.providers.usage_budget import BudgetCounts, BudgetLimits


class ProviderUsage(Namespace):
    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    as_of: UTCDateTime
    resets_at: UTCDateTime
    counts: BudgetCounts
    limits: BudgetLimits | None
    available: BudgetLimits | None


class UsageMetrics:
    def __init__(self, policy: Policy, budgets: ProviderBudgets) -> None:
        self.policy, self.budgets = policy, budgets

    async def get(self, access: AccessContext, kg: UUID) -> ProviderUsage:
        current = await self.policy.revalidate(access, action=Action.CONFIGURE)
        if kg not in current.kg_ids:
            raise inaccessible_reference()
        timestamp = datetime.now(UTC)
        day = timestamp.date().isoformat()
        raw = await self.policy.database.provider_budget_days.find_one(
            {"org_id": str(current.org_id), "kg_id": str(kg), "scope": "kg", "day": day}
        )
        try:
            counts = BudgetCounts.model_validate(raw["counts"]) if raw else BudgetCounts()
        except (ValidationError, KeyError, TypeError):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "The KG budget counters are inconsistent."
            ) from None
        limits = self.budgets.limits.knowledge_base
        available = (
            BudgetLimits.model_validate(
                {
                    field: max(
                        0,
                        getattr(limits, field)
                        - getattr(counts, "spent_" + field)
                        - getattr(counts, "reserved_" + field),
                    )
                    for field in BudgetLimits.model_fields
                }
            )
            if limits is not None
            else None
        )
        await self.policy.revalidate(current, action=Action.CONFIGURE)
        return ProviderUsage(
            org_id=current.org_id,
            kg_id=kg,
            day=day,
            as_of=timestamp,
            resets_at=datetime.combine(
                timestamp.date() + timedelta(days=1), datetime.min.time(), UTC
            ),
            counts=counts,
            limits=limits,
            available=available,
        )
