"""Owner-scoped immutable audit fields deliberately omit command and source payloads."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import Field, model_validator
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.queue import JobQueue
from domain_kg.models import Namespace, PageRequest, ScopedRef, UTCDateTime
from domain_kg.operations.models import OwnerOperationKind


class AuditEvent(ScopedRef):
    operation_id: UUID
    job_id: UUID
    actor_id: UUID
    kind: OwnerOperationKind
    outcome: Literal["admitted", "cancelled", "retried", "completed", "failed"]
    created_at: UTCDateTime
    attempt_id: UUID | None = None
    error_code: ErrorCode | None = None
    failure_reason: Literal["worker_error", "lease_expired"] | None = None

    @model_validator(mode="after")
    def failure_fields(self) -> Self:
        if (self.attempt_id is None) != (self.error_code is None) or (
            self.outcome != "failed"
            and any(
                value is not None
                for value in (self.attempt_id, self.error_code, self.failure_reason)
            )
        ):
            raise ValueError("failure provenance must be typed and belong to a failed attempt")
        if self.failure_reason is not None and self.attempt_id is None:
            raise ValueError("a failure reason requires its actual attempt")
        return self


class AuditPage(Namespace):
    items: Annotated[tuple[AuditEvent, ...], Field(max_length=100)]
    next_cursor: str | None = None

    @model_validator(mode="after")
    def scoped_events(self) -> Self:
        if any((item.org_id, item.kg_id) != (self.org_id, self.kg_id) for item in self.items):
            raise ValueError("audit events must share the page namespace")
        return self


class AuditTrail:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database = queue, queue.database

    async def append(
        self,
        access: AccessContext,
        kg: UUID,
        operation: UUID,
        job: UUID,
        kind: OwnerOperationKind,
        outcome: Literal["admitted", "cancelled", "retried", "completed", "failed"],
        session: AsyncClientSession,
    ) -> None:
        member = await self.database.members.find_one(
            {
                "org_id": str(access.org_id),
                "issuer": access.principal.issuer,
                "subject": access.principal.subject,
                "suspended": False,
            },
            session=session,
        )
        if member is None:
            raise inaccessible_reference()
        event = AuditEvent(
            org_id=access.org_id,
            kg_id=kg,
            id=uuid4(),
            operation_id=operation,
            job_id=job,
            actor_id=UUID(member["id"]),
            kind=kind,
            outcome=outcome,
            created_at=datetime.now(UTC),
        )
        await self.database.operation_audit.insert_one(
            event.model_dump(mode="json"), session=session
        )

    async def list(self, access: AccessContext, kg: UUID, page: PageRequest) -> AuditPage:
        current = await self.queue.policy.revalidate(access, action=Action.CONFIGURE)
        if kg not in current.kg_ids:
            raise inaccessible_reference()
        ns = {"org_id": str(current.org_id), "kg_id": str(kg)}
        binding = ns | {
            "issuer": current.principal.issuer,
            "subject": current.principal.subject,
            "principal_kind": current.principal.kind,
            "policy_revision": current.policy_revision,
            "limit": page.limit,
        }
        after = ""
        if page.cursor:
            cursor = await self.database.operation_audit_cursors.find_one(
                {
                    "_id": hashlib.sha256(page.cursor.encode()).hexdigest(),
                    "expires_at": {"$gt": datetime.now(UTC)},
                }
            )
            if cursor is None or any(cursor.get(key) != value for key, value in binding.items()):
                raise DomainError(ErrorCode.CURSOR_EXPIRED, "The audit cursor scope changed.")
            after = cursor["after"]
        rows = (
            await self.database.operation_audit.find(ns | {"id": {"$gt": after}})
            .sort("id", 1)
            .limit(page.limit + 1)
            .to_list()
        )
        token = None
        if len(rows) > page.limit:
            token = secrets.token_urlsafe(32)
            await self.database.operation_audit_cursors.create_index(
                "expires_at", expireAfterSeconds=0
            )
            await self.database.operation_audit_cursors.insert_one(
                binding
                | {
                    "_id": hashlib.sha256(token.encode()).hexdigest(),
                    "after": rows[page.limit - 1]["id"],
                    "expires_at": datetime.now(UTC) + timedelta(minutes=15),
                }
            )
        result = AuditPage(
            org_id=current.org_id,
            kg_id=kg,
            items=tuple(
                AuditEvent.model_validate(
                    {key: value for key, value in row.items() if key != "_id"}
                )
                for row in rows[: page.limit]
            ),
            next_cursor=token,
        )
        await self.queue.policy.revalidate(current, action=Action.CONFIGURE)
        return result
