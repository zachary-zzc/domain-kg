"""Failure metadata preserves verified admission provenance without reviving old grants."""

from typing import Any, Literal
from uuid import UUID, uuid5

from pydantic import ValidationError
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Principal
from domain_kg.jobs.queue import TERMINAL, JobQueue, now
from domain_kg.materials.service import digest
from domain_kg.operations.audit import AuditEvent
from domain_kg.operations.jobs import OWNER_KINDS
from domain_kg.operations.models import OwnerOperation


class FailureAudit:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database = queue, queue.database

    async def append(
        self,
        raw: dict[str, Any],
        code: ErrorCode,
        session: AsyncClientSession,
        *,
        reason: Literal["worker_error", "lease_expired"] = "worker_error",
    ) -> None:
        ns = {"org_id": raw["org_id"], "kg_id": raw["kg_id"]}
        try:
            stored = await self.database.owner_operations.find_one(
                ns | {"id": raw["target_id"]}, session=session
            )
            if stored is None:
                raise ValueError("missing admission")
            operation = OwnerOperation.model_validate(
                {key: stored[key] for key in OwnerOperation.model_fields}
            )
            if (
                str(operation.job.id),
                str(operation.id),
                operation.command.kind,
                operation.actor,
                operation.command.idempotency_key,
                operation.intent_hash,
            ) != (
                raw["id"],
                raw["target_id"],
                raw["kind"],
                Principal.model_validate(raw["actor"]),
                raw["idempotency_key"],
                digest(operation.command.model_dump(mode="json")),
            ):
                raise ValueError("admission differs")
            admission = await self.database.operation_audit.find_one(
                ns
                | {
                    "operation_id": str(operation.id),
                    "job_id": str(operation.job.id),
                    "kind": operation.command.kind,
                    "outcome": "admitted",
                },
                session=session,
            )
            if admission is None:
                raise ValueError("missing actor provenance")
            anchor = AuditEvent.model_validate(
                {key: value for key, value in admission.items() if key != "_id"}
            )
            attempt = UUID(raw["attempt_id"])
            event = AuditEvent(
                org_id=operation.org_id,
                kg_id=operation.kg_id,
                id=uuid5(attempt, "owner-operation-failed"),
                operation_id=operation.id,
                job_id=operation.job.id,
                actor_id=anchor.actor_id,
                kind=operation.command.kind,
                outcome="failed",
                created_at=now(),
                attempt_id=attempt,
                error_code=code,
                failure_reason=reason,
            )
        except (ValidationError, ValueError, KeyError, TypeError):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Owner failure provenance is inconsistent."
            ) from None
        # The actor ID is the immutable admitted actor, not a present authorization.
        # This records an outcome only; it grants no data access or mutation rights.
        await self.database.operation_audit.insert_one(
            event.model_dump(mode="json"), session=session
        )

    async def expire(self, allowed: tuple[str, ...]) -> None:
        kinds = sorted(OWNER_KINDS.intersection(allowed))
        if not kinds:
            return
        query = {
            "kind": {"$in": kinds},
            "stage": {"$nin": list(TERMINAL)},
            "attempts": {"$gte": self.queue.max_attempts},
            "lease_until": {"$lte": now()},
        }
        rows = await self.database.jobs.find(query).limit(100).to_list()
        for row in rows:

            async def finish(session: AsyncClientSession, row: dict[str, Any] = row) -> None:
                current = await self.database.jobs.find_one(
                    query
                    | {"_id": row["_id"], "attempt_id": row["attempt_id"], "fence": row["fence"]},
                    session=session,
                )
                if current is None:
                    return
                changed = await self.database.jobs.update_one(
                    {"_id": current["_id"], "fence": current["fence"]},
                    {
                        "$set": {
                            "stage": "failed",
                            "error_code": "VERSION_CONFLICT",
                            "lease_until": now(),
                        }
                    },
                    session=session,
                )
                if not changed.matched_count:
                    raise DomainError(ErrorCode.VERSION_CONFLICT, "Expired job changed.")
                await self.append(
                    current, ErrorCode.VERSION_CONFLICT, session, reason="lease_expired"
                )

            await self.queue.transactions.transaction(finish)
