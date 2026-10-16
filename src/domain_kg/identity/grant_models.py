"""Typed owner grant commands and permission-filtered inspection results."""

from typing import Literal
from uuid import UUID

from domain_kg.models import FrozenModel, IdempotencyKey, NonNegative


class MemberRoleChange(FrozenModel):
    kg_id: UUID
    subject_kind: Literal["member", "group"]
    subject_id: UUID
    role: Literal["reader", "editor", "owner"] | None
    expected_revision: NonNegative
    idempotency_key: IdempotencyKey


class GrantSummary(FrozenModel):
    subject_kind: Literal["member", "group"]
    subject_id: UUID
    role: Literal["reader", "editor", "owner"]


class GrantPage(FrozenModel):
    items: tuple[GrantSummary, ...]
    policy_revision: NonNegative
    next_cursor: str | None = None


class PolicyReceipt(FrozenModel):
    policy_revision: NonNegative
