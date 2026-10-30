"""Versioned public commands never accept caller identity, roles or connection secrets."""

from datetime import UTC, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BeforeValidator, Field, model_validator

from domain_kg.identity.models import Action
from domain_kg.models import FrozenModel, IdempotencyKey, NonNegative, UTCDateTime

Name = Annotated[str, Field(min_length=1, max_length=200)]
Ids = Annotated[tuple[UUID, ...], Field(max_length=100)]
Actions = Annotated[tuple[Action, ...], Field(min_length=1, max_length=20)]
StoredTime = Annotated[
    UTCDateTime,
    BeforeValidator(
        lambda value: (
            value.replace(tzinfo=UTC)
            if isinstance(value, datetime) and value.tzinfo is None
            else value
        )
    ),
]
CONTROL_ACTIONS = frozenset({Action.CREATE_KG, Action.MANAGE_ORGANIZATION, Action.REGISTER_BACKEND})


class DefaultGrant(FrozenModel):
    kg_id: UUID
    role: Literal["reader", "editor"]


class GroupMapping(FrozenModel):
    external_group: Name
    group_id: UUID


class IntegrationCreate(FrozenModel):
    profile_id: UUID
    name: Name
    allowed_kg_ids: Ids = ()
    allowed_actions: Actions = (Action.READ, Action.SEARCH)
    default_grants: Annotated[tuple[DefaultGrant, ...], Field(max_length=100)] = ()
    group_mappings: Annotated[tuple[GroupMapping, ...], Field(max_length=100)] = ()
    auto_approve: bool = False
    idempotency_key: IdempotencyKey

    @model_validator(mode="after")
    def bounded_authority(self) -> Self:
        if len(set(self.allowed_kg_ids)) != len(self.allowed_kg_ids) or len(
            set(self.allowed_actions)
        ) != len(self.allowed_actions):
            raise ValueError("IDs and actions must be distinct")
        if CONTROL_ACTIONS.intersection(self.allowed_actions):
            raise ValueError("organization/infrastructure administration cannot be delegated")
        if not {g.kg_id for g in self.default_grants} <= set(self.allowed_kg_ids):
            raise ValueError("default grants must belong to permitted KGs")
        if len({g.kg_id for g in self.default_grants}) != len(self.default_grants):
            raise ValueError("default grants must be distinct")
        if len({g.external_group for g in self.group_mappings}) != len(self.group_mappings) or len(
            {g.group_id for g in self.group_mappings}
        ) != len(self.group_mappings):
            raise ValueError("group mappings must be distinct")
        return self


class IntegrationView(FrozenModel):
    id: UUID
    org_id: UUID
    profile_id: UUID
    name: str
    allowed_kg_ids: tuple[UUID, ...]
    allowed_actions: tuple[Action, ...]
    default_grants: tuple[DefaultGrant, ...]
    group_mappings: tuple[GroupMapping, ...] = ()
    auto_approve: bool
    active: bool
    revision: NonNegative


class IntegrationChange(FrozenModel):
    expected_revision: NonNegative
    idempotency_key: IdempotencyKey
    active: bool


class AssertionRequest(FrozenModel):
    integration_id: UUID
    subject_token_type: Literal["urn:ietf:params:oauth:token-type:jwt"]
    subject_token: Annotated[str, Field(min_length=1, max_length=16384, repr=False)]


class TokenRequest(AssertionRequest):
    kg_ids: Ids
    actions: Actions = (Action.READ, Action.SEARCH)
    connection_seconds: Annotated[int, Field(ge=60, le=604800)] = 86400

    @model_validator(mode="after")
    def distinct_scope(self) -> Self:
        if len(set(self.kg_ids)) != len(self.kg_ids) or len(set(self.actions)) != len(self.actions):
            raise ValueError("KG IDs/actions must be distinct")
        if CONTROL_ACTIONS.intersection(self.actions):
            raise ValueError("control-plane actions cannot be delegated")
        return self


class BootstrapResult(FrozenModel):
    member_id: UUID
    created: bool
    status: Literal["active", "pending_approval", "suspended"]
    connection_allowed: bool


class TokenResult(FrozenModel):
    access_token: str = Field(repr=False)
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: Annotated[int, Field(ge=1, le=600)]
    connection_id: UUID
    connection_expires_at: UTCDateTime


class ConnectionView(FrozenModel):
    id: UUID
    org_id: UUID
    integration_id: UUID
    member_id: UUID
    application: str
    kg_ids: tuple[UUID, ...]
    actions: tuple[Action, ...]
    created_at: StoredTime
    expires_at: StoredTime
    revoked: bool
    revision: NonNegative
    last_used_at: StoredTime | None = None


class RevisionCommand(FrozenModel):
    expected_revision: NonNegative
    idempotency_key: IdempotencyKey


class LinkApproval(RevisionCommand):
    approved: bool


class SyncEvent(FrozenModel):
    integration_id: UUID
    event_id: Name
    external_subject: Annotated[str, Field(min_length=1, max_length=512)]
    source_version: Annotated[int, Field(ge=1, strict=True)]
    operation: Literal["disable", "enable", "profile"]
    display_name: Name | None = None
    groups: Annotated[tuple[Name, ...], Field(max_length=100)] | None = None


class EventResult(FrozenModel):
    event_id: str
    applied: bool
    source_version: int
    recovery_required: bool = False


class IntegrationPage(FrozenModel):
    items: tuple[IntegrationView, ...]
    next_cursor: str | None = None


class ConnectionPage(FrozenModel):
    items: tuple[ConnectionView, ...]
    next_cursor: str | None = None


class LinkView(FrozenModel):
    id: UUID
    member_id: UUID
    integration_id: UUID
    approved: bool
    disabled: bool
    revision: NonNegative


class LinkPage(FrozenModel):
    items: tuple[LinkView, ...]
    next_cursor: str | None = None


class IntegrationRecord(IntegrationView):
    canonical_issuer: str
    exchange_client_id: str
    profile_fingerprint: str


class IdentityLink(LinkView):
    org_id: UUID
    external_subject: str
    external_issuer: str
    canonical_issuer: str
    canonical_subject: str
    source_version: int = 0
    display_name: str | None = None
    synced_group_ids: tuple[UUID, ...] = ()


class ConnectionRecord(ConnectionView):
    issuer: str
    subject: str
    link_id: UUID


class TokenBinding(FrozenModel):
    token_hash: str
    connection_id: UUID
    org_id: UUID
    issuer: str
    subject: str
    client_id: str
    expires_at: StoredTime
