"""Conservative provider-call bounds; uncertainty cannot release possible spend."""

from typing import Annotated, Literal

from pydantic import Field

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.models import FrozenModel

BudgetAmount = Annotated[int, Field(ge=0, le=10**12, strict=True)]
BudgetOutcome = Literal["reported", "not_attempted", "unknown"]


class BudgetLimits(FrozenModel):
    input_tokens: BudgetAmount = 2_000_000
    output_tokens: BudgetAmount = 512_000
    calls: Annotated[int, Field(ge=0, le=1_000_000, strict=True)] = 1000


class BudgetPolicy(FrozenModel):
    organization: BudgetLimits | None = BudgetLimits(
        input_tokens=8_000_000, output_tokens=2_048_000, calls=4000
    )
    knowledge_base: BudgetLimits | None = BudgetLimits()


class BudgetBounds(FrozenModel):
    input_tokens: Annotated[int, Field(ge=0, le=1_000_000, strict=True)]
    output_tokens: Annotated[int, Field(ge=0, le=16384, strict=True)] = 0


class BudgetCounts(FrozenModel):
    reserved_input_tokens: BudgetAmount = 0
    reserved_output_tokens: BudgetAmount = 0
    reserved_calls: BudgetAmount = 0
    spent_input_tokens: BudgetAmount = 0
    spent_output_tokens: BudgetAmount = 0
    spent_calls: BudgetAmount = 0


def reserve(
    counts: BudgetCounts, bounds: BudgetBounds, limits: BudgetLimits | None
) -> BudgetCounts:
    changes = counts.model_dump()
    for field, amount in (
        ("input_tokens", bounds.input_tokens),
        ("output_tokens", bounds.output_tokens),
        ("calls", 1),
    ):
        if limits is not None and changes[f"reserved_{field}"] + changes[
            f"spent_{field}"
        ] + amount > getattr(limits, field):
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "The provider usage budget is exhausted.")
        changes[f"reserved_{field}"] += amount
    return BudgetCounts.model_validate(changes)


def settle(
    counts: BudgetCounts,
    bounds: BudgetBounds,
    *,
    outcome: BudgetOutcome,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> BudgetCounts:
    if (
        outcome not in ("reported", "not_attempted", "unknown")
        or type(input_tokens) is not int
        or type(output_tokens) is not int
        or not 0 <= input_tokens <= bounds.input_tokens
        or not 0 <= output_tokens <= bounds.output_tokens
        or outcome != "reported"
        and (input_tokens or output_tokens)
    ):
        raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Provider budget usage differs.")
    actual = (
        (input_tokens, output_tokens, 1)
        if outcome == "reported"
        else (bounds.input_tokens, bounds.output_tokens, 1)
        if outcome == "unknown"
        else (0, 0, 0)
    )
    changes = counts.model_dump()
    for field, held, spent in zip(
        ("input_tokens", "output_tokens", "calls"),
        (bounds.input_tokens, bounds.output_tokens, 1),
        actual,
        strict=True,
    ):
        if changes[f"reserved_{field}"] < held:
            raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Provider budget hold is missing.")
        changes[f"reserved_{field}"] -= held
        changes[f"spent_{field}"] += spent
    return BudgetCounts.model_validate(changes)
