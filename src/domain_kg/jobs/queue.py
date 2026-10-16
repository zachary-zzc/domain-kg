"""Durable authority jobs. Every worker update requires a current fenced lease."""

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError
from pymongo import ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext, Action, Principal
from domain_kg.identity.policy import Policy
from domain_kg.jobs.models import (
    ATTEMPT_STAGES,
    Checkpoint,
    JobArtifact,
    JobCommand,
    JobReceipt,
    LeasedJob,
    PreparationStage,
    PublicationLease,
)
from domain_kg.materials.models import BlobRef, BlobScope
from domain_kg.materials.repository import MaterialRepository
from domain_kg.models import SHA256, ScopedRef
from domain_kg.storage.blobs import BlobStore
from domain_kg.storage.integrity import NamespaceRepository
from domain_kg.storage.router import KGStoreRouter

TERMINAL = ("ready", "ready_with_warnings", "cancelled", "failed")
STAGES = (
    "validating",
    "parsing",
    "chunking",
    "extracting",
    "resolving",
    "embedding",
    "projecting",
    "verifying",
    "publishing",
)


def now() -> datetime:
    return datetime.now(UTC)


def lease_conflict() -> DomainError:
    return DomainError(ErrorCode.VERSION_CONFLICT, "The worker lease is no longer current.")


class JobQueue:
    def __init__(
        self,
        policy: Policy,
        router: KGStoreRouter,
        *,
        lease_seconds: int = 30,
        max_attempts: int = 3,
    ) -> None:
        if not 3 <= lease_seconds <= 300 or not 1 <= max_attempts <= 5:
            raise ValueError("job lease/retry bounds exceeded")
        self.policy, self.router, self.database = policy, router, policy.database
        self.lease_seconds, self.max_attempts = lease_seconds, max_attempts
        self.transactions = MaterialRepository(policy)
        self._authorities: dict[tuple[UUID, UUID], NamespaceRepository] = {}

    async def initialize(self) -> None:
        await self.transactions.initialize()
        await self.database.jobs.create_index(
            [
                ("org_id", 1),
                ("kg_id", 1),
                ("actor.issuer", 1),
                ("actor.subject", 1),
                ("idempotency_key", 1),
            ],
            unique=True,
        )
        await self.database.jobs.create_index(
            [("stage", 1), ("available_at", 1), ("created_at", 1)]
        )
        await self.database.jobs.create_index([("stage", 1), ("lease_until", 1)])
        await self.database.job_checkpoints.create_index(
            [
                ("org_id", 1),
                ("kg_id", 1),
                ("job_id", 1),
                ("stage", 1),
                ("input_hash", 1),
                ("attempt_id", 1),
            ],
            unique=True,
        )
        await self.database.job_commands.create_index(
            [
                ("org_id", 1),
                ("kg_id", 1),
                ("issuer", 1),
                ("subject", 1),
                ("key", 1),
            ],
            unique=True,
        )

        await self.database.job_artifacts.create_index(
            [("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True
        )

    @staticmethod
    def _scope(job: JobCommand) -> dict[str, str]:
        return {"org_id": str(job.org_id), "kg_id": str(job.kg_id), "id": str(job.id)}

    @classmethod
    def _active(cls, job: LeasedJob) -> dict[str, Any]:
        return cls._scope(job) | {
            "kind": job.kind,
            "target_id": str(job.target_id),
            "lease_token": str(job.lease_token),
            "fence": job.fence,
            "attempt_id": str(job.attempt_id),
            "lease_until": {"$gt": now()},
            "stage": {"$nin": TERMINAL},
        }

    @staticmethod
    def _lease(raw: dict[str, Any]) -> LeasedJob:
        values = {key: raw[key] for key in LeasedJob.model_fields}
        values["lease_until"] = values["lease_until"].replace(tzinfo=UTC)
        return LeasedJob.model_validate(values)

    @staticmethod
    def _receipt(raw: dict[str, Any]) -> JobReceipt:
        return JobReceipt(
            org_id=raw["org_id"],
            kg_id=raw["kg_id"],
            job_id=raw["id"],
            stage=raw["stage"],
            progress=raw.get("progress", 0),
            warnings=tuple(raw.get("warnings", ()))
            + ((raw["error_code"],) if raw.get("error_code") else ()),
        )

    @staticmethod
    def _action(kind: str) -> Action:
        if kind not in ("ingest", "replace", "repair"):
            raise DomainError(
                ErrorCode.VALIDATION_ERROR, "This job kind has no configured handler."
            )
        return Action.REPLACE if kind == "replace" else Action.INGEST

    async def _source(
        self, access: AccessContext, command: JobCommand, session: AsyncClientSession | None = None
    ) -> UUID:
        if command.org_id != access.org_id or command.kg_id not in access.kg_ids:
            raise inaccessible_reference()
        key = (command.org_id, command.kg_id)
        if session is None:
            stores = await self.router.for_kg(access, command.kg_id)
            self._authorities[key] = stores.authority
        authority = self._authorities.get(key)
        if authority is None:
            raise DomainError(
                ErrorCode.REVISION_NOT_READY, "Bind the source before opening a transaction."
            )
        reference = ScopedRef(org_id=command.org_id, kg_id=command.kg_id, id=command.target_id)
        raw = await authority._require("material_versions", reference, session=session)
        await authority.require_version(reference, session=session)
        material = UUID(raw["material"]["id"])
        if not await self.policy.material_visible(access, command.kg_id, material, session=session):
            raise inaccessible_reference()
        if command.kind == "repair":
            active = await self.database.material_access.find_one(
                {
                    "org_id": str(command.org_id),
                    "kg_id": str(command.kg_id),
                    "id": str(material),
                    "active_version_id": str(command.target_id),
                },
                session=session,
            )
            if active is None:
                raise DomainError(
                    ErrorCode.VERSION_CONFLICT, "Only the current accepted source can be repaired."
                )
        return material

    async def enqueue(self, access: AccessContext, command: JobCommand) -> JobReceipt:
        command = JobCommand.model_validate(command)
        current = await self.policy.revalidate(access, action=self._action(command.kind))
        material = await self._source(current, command)

        async def accept(session: AsyncClientSession) -> JobReceipt:
            await self.transactions.fence(current, self._action(command.kind), session)
            await self._source(current, command, session)
            key = {
                "org_id": str(command.org_id),
                "kg_id": str(command.kg_id),
                "actor.issuer": current.principal.issuer,
                "actor.subject": current.principal.subject,
                "idempotency_key": command.idempotency_key,
            }
            previous = await self.database.jobs.find_one(key, session=session)
            if previous:
                if any(
                    previous[field] != value
                    for field, value in command.model_dump(mode="json").items()
                ):
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "Job key has different input."
                    )
                return self._receipt(previous)
            raw = command.model_dump(mode="json") | {
                "actor": current.principal.model_dump(mode="json"),
                "material_id": str(material),
                "stage": "queued",
                "fence": 0,
                "attempts": 0,
                "created_at": now(),
                "expected_active_version": None,
            }
            if command.kind == "repair":
                kg = await self.database.kgs.find_one(
                    {"org_id": str(command.org_id), "id": str(command.kg_id)}, session=session
                )
                binding = await self.database.publication_bindings.find_one(
                    {
                        "org_id": str(command.org_id),
                        "kg_id": str(command.kg_id),
                        "view_manifest_id": kg.get("published_manifest_id") if kg else None,
                        "owner_key": f"source:{material}",
                    },
                    session=session,
                )
                if binding is None or binding["binding"]["source"]["version_id"] != str(
                    command.target_id
                ):
                    raise DomainError(
                        ErrorCode.VERSION_CONFLICT, "The accepted source mapping changed."
                    )
                raw.update(
                    expected_active_version=str(command.target_id),
                    repair_parent_manifest_id=binding["binding"]["manifest_id"],
                )
            await self.database.jobs.insert_one(raw, session=session)
            await self.database.outbox.insert_one(
                self._scope(command)
                | {
                    "kind": "job_available",
                    "delivered": False,
                    "created_at": now(),
                },
                session=session,
            )
            return self._receipt(raw)

        receipt = await self.transactions.transaction(accept)
        await self._source(current, command)
        return receipt

    async def claim(
        self, worker_id: str, *, kinds: tuple[str, ...] | None = None
    ) -> LeasedJob | None:
        if not isinstance(worker_id, str) or not worker_id or len(worker_id) > 100:
            raise ValueError("invalid worker identifier")
        allowed = kinds if kinds is not None else ("ingest", "replace", "repair")
        if not allowed or not set(allowed).issubset({"ingest", "replace", "repair"}):
            raise ValueError("job kinds require a configured handler")
        # Expired leases at the bound become explicit terminal failures; no
        # unbounded polling or accidental fourth attempt after worker death.
        await self.database.jobs.update_many(
            {
                "kind": {"$in": list(allowed)},
                "stage": {"$nin": TERMINAL},
                "attempts": {"$gte": self.max_attempts},
                "lease_until": {"$lte": now()},
            },
            {"$set": {"stage": "failed", "error_code": "RETRY_EXHAUSTED"}},
        )
        raw = await self.database.jobs.find_one_and_update(
            {
                "kind": {"$in": list(allowed)},
                "attempts": {"$lt": self.max_attempts},
                "$or": [
                    {
                        "stage": {"$in": ["queued", "repair_required"]},
                        "$and": [
                            {
                                "$or": [
                                    {"available_at": {"$lte": now()}},
                                    {"available_at": {"$exists": False}},
                                ]
                            },
                            {
                                "$or": [
                                    {"lease_until": {"$lte": now()}},
                                    {"lease_until": {"$exists": False}},
                                ]
                            },
                        ],
                    },
                    {"stage": {"$in": list(STAGES)}, "lease_until": {"$lte": now()}},
                ],
            },
            {
                "$set": {
                    "lease_token": str(uuid4()),
                    "attempt_id": str(uuid4()),
                    "lease_until": now() + timedelta(seconds=self.lease_seconds),
                    "worker_id": worker_id,
                    "stage": "validating",
                },
                "$inc": {"fence": 1, "attempts": 1, "total_attempts": 1},
            },
            sort=[("created_at", 1), ("id", 1)],
            return_document=ReturnDocument.AFTER,
        )
        if raw is None:
            return None
        # A crash before this acknowledgement leaves only a duplicate wake-up;
        # the job itself is already atomically leased, never enqueued twice.
        await self.database.outbox.update_one(
            self._scope(self._lease(raw)), {"$set": {"delivered": True}}
        )
        return self._lease(raw)

    async def _current(
        self, job: LeasedJob, session: AsyncClientSession | None = None
    ) -> dict[str, Any]:
        job = LeasedJob.model_validate(job)
        raw = await self.database.jobs.find_one(self._active(job), session=session)
        if raw is None:
            raise lease_conflict()
        return raw

    async def authorize(
        self, job: LeasedJob, session: AsyncClientSession | None = None
    ) -> AccessContext:
        raw = await self._current(job, session)
        access = await self.policy.resolve_access(
            Principal.model_validate(raw["actor"]),
            [job.kg_id],
            self._action(job.kind),
            org_id=job.org_id,
            session=session,
        )
        material = await self._source(access, job, session)
        if str(material) != raw["material_id"]:
            raise inaccessible_reference()
        target = await self.database.material_access.find_one(
            {
                "org_id": str(job.org_id),
                "kg_id": str(job.kg_id),
                "id": str(material),
            },
            session=session,
        )
        if target is None or (
            raw.get("expected_active_version") is not None
            and target.get("active_version_id") != raw["expected_active_version"]
        ):
            raise DomainError(ErrorCode.VERSION_CONFLICT, "The source version changed.")
        return access

    async def heartbeat(self, job: LeasedJob) -> LeasedJob:
        raw = await self.database.jobs.find_one_and_update(
            self._active(job),
            {"$set": {"lease_until": now() + timedelta(seconds=self.lease_seconds)}},
            return_document=ReturnDocument.AFTER,
        )
        if raw is None:
            raise lease_conflict()
        await self.database.kgs.update_one(
            {
                "org_id": str(job.org_id),
                "id": str(job.kg_id),
                "publication_lease.job_id": str(job.id),
                "publication_lease.job_lease_token": str(job.lease_token),
                "publication_lease.attempt_id": str(job.attempt_id),
                "publication_lease.lease_until": {"$gt": now()},
            },
            {
                "$set": {
                    "publication_lease.lease_until": now() + timedelta(seconds=self.lease_seconds)
                }
            },
        )
        return self._lease(raw)

    async def store_artifact(
        self, job: LeasedJob, stage: PreparationStage, input_hash: str, data: bytes
    ) -> Checkpoint:
        stage = TypeAdapter(PreparationStage).validate_python(stage)
        input_hash = TypeAdapter(SHA256).validate_python(input_hash)
        access = await self.authorize(job)
        raw = await self._current(job)
        stores = await self.router.for_kg(access, job.kg_id)
        scope = BlobScope(
            org_id=job.org_id,
            kg_id=job.kg_id,
            material_id=raw["material_id"],
            version_id=job.target_id,
        )

        async def stream() -> AsyncIterator[bytes]:
            for offset in range(0, len(data), 65536):
                yield data[offset : offset + 65536]

        blob = await BlobStore(stores.blobs).put(
            stream(), hashlib.sha256(data).hexdigest(), scope, media_type="application/json"
        )
        artifact = JobArtifact(
            org_id=job.org_id,
            kg_id=job.kg_id,
            id=uuid4(),
            job_id=job.id,
            source_version_id=job.target_id,
            stage=stage,
            input_hash=input_hash,
            blob=blob,
        )

        async def save(session: AsyncClientSession) -> None:
            await self.authorize(job, session)
            await self.database.job_artifacts.insert_one(
                artifact.model_dump(mode="json"), session=session
            )
            result = await self.database.jobs.update_one(
                self._active(job), {"$inc": {"artifact_epoch": 1}}, session=session
            )
            if not result.matched_count:
                raise lease_conflict()

        await self.transactions.transaction(save)
        return Checkpoint(
            org_id=job.org_id,
            kg_id=job.kg_id,
            job_id=job.id,
            stage=stage,
            artifact_id=artifact.id,
            input_hash=input_hash,
            output_hash=blob.sha256,
            attempt_id=job.attempt_id if stage in ATTEMPT_STAGES else None,
        )

    async def artifact_blob(
        self, job: LeasedJob, checkpoint: Checkpoint, session: AsyncClientSession | None = None
    ) -> BlobRef:
        if (checkpoint.org_id, checkpoint.kg_id, checkpoint.job_id) != (
            job.org_id,
            job.kg_id,
            job.id,
        ):
            raise inaccessible_reference()
        raw = await self.database.job_artifacts.find_one(
            {
                "org_id": str(job.org_id),
                "kg_id": str(job.kg_id),
                "id": str(checkpoint.artifact_id),
                "job_id": str(job.id),
                "source_version_id": str(job.target_id),
                "stage": checkpoint.stage,
                "input_hash": checkpoint.input_hash,
            },
            session=session,
        )
        if raw is None:
            raise inaccessible_reference()
        try:
            artifact = JobArtifact.model_validate(
                {key: value for key, value in raw.items() if key != "_id"}
            )
        except ValidationError as exc:
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "The stored artifact has invalid ownership."
            ) from exc
        source = await self._current(job, session)
        if (
            artifact.blob.sha256 != checkpoint.output_hash
            or str(artifact.blob.material_id) != source["material_id"]
        ):
            raise inaccessible_reference()
        return artifact.blob

    async def checkpoint(self, job: LeasedJob, checkpoint: Checkpoint) -> None:
        checkpoint = Checkpoint.model_validate(checkpoint)
        if (checkpoint.org_id, checkpoint.kg_id, checkpoint.job_id) != (
            job.org_id,
            job.kg_id,
            job.id,
        ) or (checkpoint.stage in ATTEMPT_STAGES and checkpoint.attempt_id != job.attempt_id):
            raise lease_conflict()
        value = checkpoint.model_dump(mode="json")
        key = {
            field: value[field]
            for field in ("org_id", "kg_id", "job_id", "stage", "input_hash", "attempt_id")
        }

        async def save(session: AsyncClientSession) -> None:
            await self._current(job, session)
            await self.artifact_blob(job, checkpoint, session)
            existing = await self.database.job_checkpoints.find_one(key, session=session)
            if existing:
                if {field: existing[field] for field in value} != value:
                    raise DomainError(ErrorCode.VERSION_CONFLICT, "Checkpoint content changed.")
            else:
                await self.database.job_checkpoints.insert_one(value, session=session)
            result = await self.database.jobs.update_one(
                self._active(job),
                {
                    "$set": {
                        "stage": checkpoint.stage,
                        "progress": (STAGES.index(checkpoint.stage) + 1) / (len(STAGES) + 1),
                    }
                },
                session=session,
            )
            if not result.matched_count:
                raise lease_conflict()

        await self.transactions.transaction(save)

    async def load_checkpoint(
        self, job: LeasedJob, stage: PreparationStage, input_hash: str
    ) -> Checkpoint | None:
        stage = TypeAdapter(PreparationStage).validate_python(stage)
        input_hash = TypeAdapter(SHA256).validate_python(input_hash)
        await self._current(job)
        raw = await self.database.job_checkpoints.find_one(
            {
                "org_id": str(job.org_id),
                "kg_id": str(job.kg_id),
                "job_id": str(job.id),
                "stage": stage,
                "input_hash": input_hash,
                "attempt_id": str(job.attempt_id) if stage in ATTEMPT_STAGES else None,
            }
        )
        if raw is None:
            return None
        checkpoint = Checkpoint.model_validate({k: v for k, v in raw.items() if k != "_id"})
        blob = await self.artifact_blob(job, checkpoint)
        access = await self.authorize(job)
        stores = await self.router.for_kg(access, job.kg_id)
        async for _ in BlobStore(stores.blobs).open(blob):
            pass
        return checkpoint

    async def fail(self, job: LeasedJob, error: DomainError, *, repair: bool = False) -> None:
        raw = await self._current(job)
        retry = error.retryable and raw["attempts"] < self.max_attempts
        stage = ("repair_required" if repair else "queued") if retry else "failed"
        code = (
            str(error.code)
            if str(error.code) in {str(value) for value in ErrorCode}
            else "BACKEND_UNAVAILABLE"
        )
        result = await self.database.jobs.update_one(
            self._active(job),
            {
                "$set": {
                    "stage": stage,
                    "error_code": code,
                    "lease_until": now(),
                    "available_at": now() + timedelta(seconds=min(5, 2 ** (raw["attempts"] - 1))),
                }
            },
        )
        if not result.matched_count:
            raise lease_conflict()

    async def finish(self, job: LeasedJob) -> None:
        if await self.accepted(job):
            return
        await self.authorize(job)

        async def commit(session: AsyncClientSession) -> None:
            await self.authorize(job, session)
            raw = await self._current(job, session)
            manifest = raw.get("accepted_manifest_id")
            accepted = (
                await self.database.publication_manifests.find_one(
                    {
                        "org_id": str(job.org_id),
                        "kg_id": str(job.kg_id),
                        "id": manifest,
                        "job_id": str(job.id),
                        "attempt_id": str(job.attempt_id),
                        "state": "accepted",
                    },
                    session=session,
                )
                if manifest
                else None
            )
            if accepted is None:
                raise DomainError(
                    ErrorCode.REVISION_NOT_READY,
                    "Publication has not been accepted.",
                    retryable=True,
                )
            result = await self.database.jobs.update_one(
                self._active(job),
                {
                    "$set": {
                        "stage": "ready",
                        "progress": 1,
                        "lease_until": now(),
                        "error_code": None,
                    }
                },
                session=session,
            )
            if not result.matched_count:
                raise lease_conflict()

        await self.transactions.transaction(commit)

    async def accepted(self, job: LeasedJob) -> bool:
        raw = await self.database.jobs.find_one(
            self._scope(job)
            | {
                "lease_token": str(job.lease_token),
                "attempt_id": str(job.attempt_id),
                "fence": job.fence,
                "stage": {"$in": ["ready", "ready_with_warnings"]},
            }
        )
        if raw is None:
            return False
        access = await self.policy.resolve_access(
            Principal.model_validate(raw["actor"]),
            [job.kg_id],
            self._action(job.kind),
            org_id=job.org_id,
        )
        if not await self.policy.material_visible(access, job.kg_id, UUID(raw["material_id"])):
            raise inaccessible_reference()
        manifest = await self.database.publication_manifests.find_one(
            {
                "org_id": str(job.org_id),
                "kg_id": str(job.kg_id),
                "id": raw.get("accepted_manifest_id"),
                "job_id": str(job.id),
                "attempt_id": str(job.attempt_id),
                "state": "accepted",
            }
        )
        if manifest is None:
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "The job acceptance marker is missing."
            )
        return True

    async def _visible_job(
        self,
        access: AccessContext,
        identifier: UUID,
        action: Action,
        session: AsyncClientSession | None = None,
    ) -> dict[str, Any]:
        current = await self.policy.revalidate(access, action=action, session=session)
        raw = await self.database.jobs.find_one(
            {
                "org_id": str(current.org_id),
                "kg_id": {"$in": [str(value) for value in current.kg_ids]},
                "id": str(identifier),
            },
            session=session,
        )
        if raw is None or not await self.policy.material_visible(
            current, UUID(raw["kg_id"]), UUID(raw["material_id"]), session=session
        ):
            raise inaccessible_reference()
        return raw

    async def get(self, access: AccessContext, identifier: UUID) -> JobReceipt:
        raw = await self._visible_job(access, identifier, Action.READ)
        receipt = self._receipt(raw)
        await self._visible_job(access, identifier, Action.READ)
        return receipt

    async def _control(
        self, access: AccessContext, identifier: UUID, key: str, operation: str
    ) -> JobReceipt:
        from pydantic import TypeAdapter

        from domain_kg.models import IdempotencyKey

        key = TypeAdapter(IdempotencyKey).validate_python(key)

        async def change(session: AsyncClientSession) -> JobReceipt:
            raw = await self._visible_job(access, identifier, Action.INGEST, session)
            current = await self.policy.revalidate(
                access, action=self._action(raw["kind"]), session=session
            )
            await self.transactions.fence(current, current.action, session)
            command_key = self.transactions.key(current, UUID(raw["kg_id"]), key)
            previous = await self.database.job_commands.find_one(command_key, session=session)
            if previous:
                if previous["operation"] != operation or previous["job_id"] != str(identifier):
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "Job-control key has different input."
                    )
                return JobReceipt.model_validate(previous["receipt"])
            if raw["stage"] in ("ready", "ready_with_warnings") or (
                operation == "retry"
                and raw["stage"] not in ("failed", "cancelled", "repair_required")
            ):
                raise DomainError(
                    ErrorCode.VERSION_CONFLICT, "This job cannot change to that state."
                )
            stage = "cancelled" if operation == "cancel" else "queued"
            result = await self.database.jobs.find_one_and_update(
                {"_id": raw["_id"]},
                {
                    "$set": {
                        "stage": stage,
                        "lease_until": now(),
                        "available_at": now(),
                        "attempts": 0 if operation == "retry" else raw["attempts"],
                        "error_code": None,
                    },
                    "$inc": {"fence": 1},
                },
                session=session,
                return_document=ReturnDocument.AFTER,
            )
            if result is None:
                raise inaccessible_reference()
            receipt = self._receipt(result)
            await self.database.job_commands.insert_one(
                command_key
                | {
                    "job_id": str(identifier),
                    "operation": operation,
                    "receipt": receipt.model_dump(mode="json"),
                },
                session=session,
            )
            return receipt

        return await self.transactions.transaction(change)

    async def cancel(
        self, access: AccessContext, identifier: UUID, *, idempotency_key: str
    ) -> JobReceipt:
        return await self._control(access, identifier, idempotency_key, "cancel")

    async def retry(
        self, access: AccessContext, identifier: UUID, *, idempotency_key: str
    ) -> JobReceipt:
        return await self._control(access, identifier, idempotency_key, "retry")

    async def acquire_publication(self, job: LeasedJob) -> PublicationLease:
        await self.authorize(job)

        async def acquire(session: AsyncClientSession) -> PublicationLease:
            access = await self.authorize(job, session)
            await self.transactions.fence(access, access.action, session)
            scope = {"org_id": str(job.org_id), "id": str(job.kg_id)}
            kg = await self.database.kgs.find_one(scope, session=session)
            if kg is None:
                raise inaccessible_reference()
            previous = kg.get("publication_lease")
            if previous and previous["lease_until"].replace(tzinfo=UTC) > now():
                live = await self.database.jobs.find_one(
                    {
                        "org_id": str(job.org_id),
                        "kg_id": str(job.kg_id),
                        "id": previous["job_id"],
                        "lease_token": previous["job_lease_token"],
                        "attempt_id": previous["attempt_id"],
                        "lease_until": {"$gt": now()},
                        "stage": {"$nin": TERMINAL},
                    },
                    session=session,
                )
                if live:
                    if (previous["job_id"], previous["attempt_id"]) == (
                        str(job.id),
                        str(job.attempt_id),
                    ):
                        return PublicationLease.model_validate(
                            previous | {"lease_until": previous["lease_until"].replace(tzinfo=UTC)}
                        )
                    raise DomainError(
                        ErrorCode.REVISION_NOT_READY,
                        "Another publisher holds this KG.",
                        retryable=True,
                    )
            lease = PublicationLease(
                org_id=job.org_id,
                kg_id=job.kg_id,
                job_id=job.id,
                job_lease_token=job.lease_token,
                attempt_id=job.attempt_id,
                lease_token=uuid4(),
                fence=kg.get("publication_fence", 0) + 1,
                lease_until=now() + timedelta(seconds=self.lease_seconds),
            )
            value = lease.model_dump(mode="json") | {"lease_until": lease.lease_until}
            await self.database.kgs.update_one(
                scope,
                {
                    "$set": {
                        "publication_lease": value,
                        "publication_fence": lease.fence,
                    }
                },
                session=session,
            )
            return lease

        return await self.transactions.transaction(acquire)

    async def validate_publication(
        self, job: LeasedJob, lease: PublicationLease, session: AsyncClientSession | None = None
    ) -> None:
        await self.authorize(job, session)
        if (lease.org_id, lease.kg_id, lease.job_id, lease.attempt_id, lease.job_lease_token) != (
            job.org_id,
            job.kg_id,
            job.id,
            job.attempt_id,
            job.lease_token,
        ):
            raise lease_conflict()
        current = await self.database.kgs.find_one(
            {
                "org_id": str(job.org_id),
                "id": str(job.kg_id),
                "publication_fence": lease.fence,
                "publication_lease.lease_token": str(lease.lease_token),
                "publication_lease.lease_until": {"$gt": now()},
            },
            session=session,
        )
        if current is None:
            raise lease_conflict()
