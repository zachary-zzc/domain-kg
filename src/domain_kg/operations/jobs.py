"""Scoped owner intent admission; no source version or completed package is fabricated."""

from typing import Any
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext, Action, Principal
from domain_kg.jobs.models import JobCommand, JobReceipt
from domain_kg.jobs.queue import JobQueue, now
from domain_kg.materials.service import digest
from domain_kg.models import ScopedRef
from domain_kg.operations.audit import AuditTrail
from domain_kg.operations.models import OwnerCommand, OwnerOperation, SnapshotPointer

OWNER_KINDS = frozenset({"backup", "export"})


def operation_action(kind: str) -> Action:
    if kind not in OWNER_KINDS:
        raise inaccessible_reference()
    return Action.BACKUP if kind == "backup" else Action.CONFIGURE


class OwnerOperations:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database = queue, queue.database
        self.audit = AuditTrail(queue)

    async def initialize(self) -> None:
        for name in ("owner_operations", "operation_audit"):
            await self.database[name].create_index(
                [("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True
            )
        await self.database.owner_operation_commands.create_index(
            [("org_id", 1), ("kg_id", 1), ("issuer", 1), ("subject", 1), ("key", 1)], unique=True
        )

    async def authorize(
        self,
        access: AccessContext,
        raw_job: dict[str, Any],
        session: AsyncClientSession | None = None,
    ) -> OwnerOperation:
        current = await self.queue.policy.revalidate(
            access, action=operation_action(raw_job["kind"]), session=session
        )
        if (
            raw_job.get("org_id") != str(current.org_id)
            or UUID(raw_job["kg_id"]) not in current.kg_ids
        ):
            raise inaccessible_reference()
        raw = await self.database.owner_operations.find_one(
            {"org_id": str(current.org_id), "kg_id": raw_job["kg_id"], "id": raw_job["target_id"]},
            session=session,
        )
        try:
            if raw is None:
                raise ValueError("missing operation")
            record = OwnerOperation.model_validate(
                {key: value for key, value in raw.items() if key != "_id"}
            )
            if (
                str(record.job.id),
                record.actor,
                record.command.kind,
                record.command.idempotency_key,
                record.intent_hash,
            ) != (
                raw_job["id"],
                Principal.model_validate(raw_job["actor"]),
                raw_job["kind"],
                raw_job["idempotency_key"],
                digest(record.command.model_dump(mode="json")),
            ):
                raise ValueError("operation ownership differs")
        except (ValueError, ValidationError, KeyError):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "The owner operation reference is inconsistent."
            ) from None
        return record

    async def submit(self, access: AccessContext, command: OwnerCommand) -> JobReceipt:
        command = TypeAdapter(OwnerCommand).validate_python(command)
        if len(command.model_dump_json().encode()) > 65536:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Operation intent exceeds its byte budget.")
        current = await self.queue.policy.revalidate(access, action=operation_action(command.kind))
        if command.kg_id not in current.kg_ids:
            raise inaccessible_reference()
        await self.initialize()
        key = self.queue.transactions.key(current, command.kg_id, command.idempotency_key)
        fingerprint = digest(command.model_dump(mode="json"))
        operation_id, job_id = uuid4(), uuid4()

        async def admit(session: AsyncClientSession) -> JobReceipt:
            await self.queue.transactions.fence(current, current.action, session)
            previous = await self.database.owner_operation_commands.find_one(key, session=session)
            if previous:
                if previous["digest"] != fingerprint:
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "Operation key has different input."
                    )
                raw = await self.database.jobs.find_one(
                    {
                        "org_id": str(current.org_id),
                        "kg_id": str(command.kg_id),
                        "id": previous["job_id"],
                    },
                    session=session,
                )
                if raw is None:
                    raise DomainError(
                        ErrorCode.PROJECTION_INCONSISTENT, "Operation job is missing."
                    )
                await self.authorize(current, raw, session)
                return self.queue._receipt(raw)
            ns = {"org_id": str(current.org_id), "kg_id": str(command.kg_id)}
            collision = await self.database.jobs.find_one(
                ns
                | {
                    "actor.issuer": current.principal.issuer,
                    "actor.subject": current.principal.subject,
                    "idempotency_key": command.idempotency_key,
                },
                session=session,
            )
            if collision is not None:
                raise DomainError(
                    ErrorCode.IDEMPOTENCY_CONFLICT, "The job key belongs to another command."
                )
            kg = await self.database.kgs.find_one(
                {"org_id": ns["org_id"], "id": ns["kg_id"]}, session=session
            )
            if kg is None:
                raise inaccessible_reference()
            if kg["published_revision"] != command.expected_published_revision:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The accepted KG revision changed.")
            snapshot = SnapshotPointer(
                org_id=current.org_id,
                kg_id=command.kg_id,
                revision=kg["published_revision"],
                generation=kg["generation"],
                manifest_id=kg.get("published_manifest_id"),
                visibility_revision=kg["visibility_revision"],
            )
            job = JobCommand(
                org_id=current.org_id,
                kg_id=command.kg_id,
                id=job_id,
                kind=command.kind,
                target_id=operation_id,
                idempotency_key=command.idempotency_key,
            )
            record = OwnerOperation(
                org_id=current.org_id,
                kg_id=command.kg_id,
                id=operation_id,
                job=ScopedRef(org_id=current.org_id, kg_id=command.kg_id, id=job_id),
                actor=current.principal,
                command=command,
                intent_hash=fingerprint,
                snapshot=snapshot,
            )
            raw = job.model_dump(mode="json") | {
                "actor": current.principal.model_dump(mode="json"),
                "stage": "queued",
                "fence": 0,
                "attempts": 0,
                "created_at": now(),
            }
            await self.database.owner_operations.insert_one(
                record.model_dump(mode="json"), session=session
            )
            await self.database.jobs.insert_one(raw, session=session)
            await self.database.owner_operation_commands.insert_one(
                key | {"digest": fingerprint, "job_id": str(job_id)}, session=session
            )
            await self.database.outbox.insert_one(
                ns
                | {
                    "id": str(job_id),
                    "kind": "job_available",
                    "job_id": str(job_id),
                    "delivered": False,
                    "created_at": now(),
                },
                session=session,
            )
            await self.audit.append(
                current, command.kg_id, operation_id, job_id, command.kind, "admitted", session
            )
            # Serialize capture admission with publication and visibility changes.
            changed = await self.database.kgs.update_one(
                {
                    "org_id": ns["org_id"],
                    "id": ns["kg_id"],
                    "published_revision": snapshot.revision,
                    "generation": str(snapshot.generation),
                    "visibility_revision": snapshot.visibility_revision,
                },
                {"$inc": {"operation_epoch": 1}},
                session=session,
            )
            if not changed.matched_count:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The operation snapshot changed.")
            return self.queue._receipt(raw)

        result = await self.queue.transactions.transaction(admit)
        await self.queue.policy.revalidate(current, action=current.action)
        return result
