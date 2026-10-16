"""Identity contexts are constructed by verified authentication and policy services."""

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from domain_kg.models import FrozenModel, NonNegative, PreservedText


class Action(StrEnum):
    LIST_KGS = "list_kgs"
    READ = "read"
    SEARCH = "search"
    INGEST = "ingest"
    REPLACE = "replace"
    REMOVE = "remove"
    CURATE = "curate"
    MANAGE_GRANTS = "manage_grants"
    CONFIGURE = "configure"
    ARCHIVE = "archive"
    BACKUP = "backup"
    RESTORE = "restore"
    PURGE = "purge"
    CREATE_KG = "create_kg"
    MANAGE_ORGANIZATION = "manage_organization"
    REGISTER_BACKEND = "register_backend"


class Principal(FrozenModel):
    issuer: Annotated[PreservedText, Field(min_length=1)]
    subject: Annotated[PreservedText, Field(min_length=1)]
    scopes: frozenset[str] = frozenset()
    kind: Literal["human", "service"] = "human"


class KGGrant(FrozenModel):
    kg_id: UUID
    role: Literal["owner", "editor", "reader"]


class AccessContext(FrozenModel):
    principal: Principal
    org_id: UUID
    kg_ids: tuple[UUID, ...] = ()
    policy_revision: NonNegative
    groups: frozenset[UUID] = frozenset()
    grants: tuple[KGGrant, ...] = ()
    action: Action = Action.READ
    organization_admin: bool = False
    platform_operator: bool = False


class Member(FrozenModel):
    id: UUID
    org_id: UUID
    issuer: PreservedText
    subject: PreservedText
    role: Literal["admin", "member"]
    kind: Literal["human", "service"] = "human"
    suspended: bool = False
    revision: NonNegative = 0
    kg_ids: tuple[UUID, ...] = ()
    scopes: frozenset[str] = frozenset()


class Group(FrozenModel):
    id: UUID
    org_id: UUID
    name: Annotated[str, Field(min_length=1, max_length=200)]
    member_ids: tuple[UUID, ...] = ()
