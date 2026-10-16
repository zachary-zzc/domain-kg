"""Authorized repair clones retained immutable data into a new projection attempt."""

import json
from uuid import UUID, uuid5

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.models import Checkpoint, JobCommand, JobReceipt, LeasedJob
from domain_kg.jobs.queue import JobQueue
from domain_kg.models import SourceVersion
from domain_kg.revisions.journal import PublicationJournal, read_artifact
from domain_kg.revisions.projections import EmbeddedDocument


class RepairService:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.journal = queue, PublicationJournal(queue)

    async def request(
        self, access: AccessContext, kg: UUID, material: UUID, *, idempotency_key: str
    ) -> JobReceipt:
        from pydantic import TypeAdapter

        from domain_kg.models import IdempotencyKey

        idempotency_key = TypeAdapter(IdempotencyKey).validate_python(idempotency_key)
        current = await self.queue.policy.revalidate(access, action=Action.INGEST)
        if not await self.queue.policy.material_visible(current, kg, material):
            raise inaccessible_reference()
        row = await self.queue.database.material_access.find_one(
            {
                "org_id": str(current.org_id),
                "kg_id": str(kg),
                "id": str(material),
            }
        )
        if row is None or not row.get("active_version_id"):
            raise DomainError(ErrorCode.REVISION_NOT_READY, "The source has no accepted version.")
        # Durable actor is the current verified caller, never the detector or original uploader.
        identity = json.dumps(
            [str(kg), current.principal.issuer, current.principal.subject, idempotency_key],
            separators=(",", ":"),
        )
        return await self.queue.enqueue(
            current,
            JobCommand(
                org_id=current.org_id,
                kg_id=kg,
                id=uuid5(current.org_id, "repair/" + identity),
                kind="repair",
                target_id=UUID(row["active_version_id"]),
                idempotency_key=idempotency_key,
            ),
        )

    async def embed(self, job: LeasedJob, *, input_hash: str) -> Checkpoint:
        if job.kind != "repair":
            raise inaccessible_reference()
        access = await self.queue.authorize(job)
        raw = await self.queue._current(job)
        parent = await self.journal.attempt(access, UUID(raw["repair_parent_manifest_id"]))
        if parent["state"] != "accepted":
            raise DomainError(ErrorCode.VERSION_CONFLICT, "The repair parent is not accepted.")
        source = SourceVersion.model_validate(parent["source"])
        if (source.org_id, source.kg_id, source.material_id, source.version_id) != (
            job.org_id,
            job.kg_id,
            UUID(raw["material_id"]),
            job.target_id,
        ):
            raise inaccessible_reference()
        checkpoint = Checkpoint.model_validate(parent["embedded_checkpoint"])
        body = await read_artifact(self.queue, access, checkpoint, source)
        import hashlib

        if hashlib.sha256(body).hexdigest() != parent["batch"]["content_hash"]:
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "The retained repair artifact differs."
            )
        document = EmbeddedDocument.model_validate_json(body)
        if document.version_id != job.target_id:
            raise inaccessible_reference()
        return await self.queue.store_artifact(job, "embedding", input_hash, body)
