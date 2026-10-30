"""Immutable source-ledger records backed by scoped, checksum-verified artifacts."""

import hashlib
from typing import Any

from pydantic import ValidationError
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.ingestion.models import ParsedDocument
from domain_kg.jobs.models import Checkpoint, JobArtifact, LeasedJob
from domain_kg.jobs.queue import JobQueue
from domain_kg.materials.models import BlobRef
from domain_kg.models import ScopedRef
from domain_kg.storage.blobs import BlobStore


class SourceRepository:
    def __init__(self, queue: JobQueue) -> None:
        self.queue = queue

    async def original(self, job: LeasedJob) -> tuple[BlobRef, BlobStore]:
        access = await self.queue.authorize(job)
        raw = await self.queue._current(job)
        stores = await self.queue.router.for_kg(access, job.kg_id)
        version = await stores.authority._database.version_blobs.find_one(
            {
                "org_id": str(job.org_id),
                "kg_id": str(job.kg_id),
                "id": str(job.target_id),
                "material_id": raw["material_id"],
                "state": {"$in": ["staged", "accepted", "retained"]},
            }
        )
        if version is None:
            raise inaccessible_reference()
        blob = BlobRef.model_validate(version["blob"])
        if (blob.org_id, blob.kg_id, blob.version_id, str(blob.material_id)) != (
            job.org_id,
            job.kg_id,
            job.target_id,
            raw["material_id"],
        ):
            raise inaccessible_reference()
        return blob, BlobStore(stores.blobs)

    async def read(self, job: LeasedJob, checkpoint: Checkpoint) -> ParsedDocument:
        original, blobs = await self.original(job)
        artifact = await self.queue.artifact_blob(job, checkpoint)
        data = bytearray()
        async for part in blobs.open(artifact):
            data.extend(part)
        document = ParsedDocument.model_validate_json(bytes(data))
        if document.blob != original:
            raise inaccessible_reference()
        await self.queue.authorize(job)
        return document

    async def persist(
        self, job: LeasedJob, document: ParsedDocument, checkpoint: Checkpoint, *, chunked: bool
    ) -> None:
        document = ParsedDocument.model_validate(document)
        original, _ = await self.original(job)
        if document.blob != original or checkpoint.stage not in (
            ("chunking",) if chunked else ("parsing", "chunking")
        ):
            raise inaccessible_reference()
        access = await self.queue.authorize(job)
        stores = await self.queue.router.for_kg(access, job.kg_id)
        collection = stores.authority._database["source_chunks" if chunked else "source_blocks"]
        await collection.create_index([("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True)
        await collection.create_index([("org_id", 1), ("kg_id", 1), ("version_id", 1)])
        await collection.create_index([("org_id", 1), ("kg_id", 1), ("artifact_id", 1)])
        records: list[dict[str, Any]] = []
        for item in document.chunks if chunked else document.blocks:
            value = item.model_dump(mode="json")
            if not chunked:
                value.pop("text")  # Large block bodies live in the immutable S3 snapshot.
            value["text_sha256"] = hashlib.sha256(item.text.encode()).hexdigest()
            value["text_size_bytes"] = len(item.text.encode())
            value["parser_version"] = document.parser_version
            records.append(value)

        async def save(session: AsyncClientSession) -> None:
            current = await self.queue.authorize(job, session)
            await self.queue.transactions.fence(current, current.action, session)
            await self.queue.artifact_blob(job, checkpoint, session)
            await stores.authority.require_version(
                ScopedRef(org_id=job.org_id, kg_id=job.kg_id, id=job.target_id), session=session
            )
            for value in records:
                key = {field: value[field] for field in ("org_id", "kg_id", "id")}
                existing = await collection.find_one(key, session=session)
                if existing:
                    if {field: existing.get(field) for field in value} != value:
                        raise DomainError(
                            ErrorCode.VERSION_CONFLICT, "Immutable source content changed."
                        )
                    artifact = await self.queue.database.job_artifacts.find_one(
                        {
                            "org_id": str(job.org_id),
                            "kg_id": str(job.kg_id),
                            "id": existing["artifact_id"],
                            "source_version_id": str(job.target_id),
                        },
                        session=session,
                    )
                    if artifact is None:
                        raise DomainError(
                            ErrorCode.PROJECTION_INCONSISTENT,
                            "Source artifact ownership is missing.",
                        )
                    try:
                        registered = JobArtifact.model_validate(
                            {
                                field: content
                                for field, content in artifact.items()
                                if field != "_id"
                            }
                        )
                    except ValidationError as exc:
                        raise DomainError(
                            ErrorCode.PROJECTION_INCONSISTENT,
                            "Source artifact ownership is invalid.",
                        ) from exc
                    origin = await self.queue.database.jobs.find_one(
                        {
                            "org_id": str(job.org_id),
                            "kg_id": str(job.kg_id),
                            "id": str(registered.job_id),
                            "target_id": str(job.target_id),
                            "material_id": str(document.blob.material_id),
                        },
                        session=session,
                    )
                    if origin is None or registered.blob.material_id != document.blob.material_id:
                        raise inaccessible_reference()
                else:
                    await collection.insert_one(
                        value
                        | {
                            "_id": f"{job.org_id}:{job.kg_id}:{value['id']}",
                            "artifact_id": str(checkpoint.artifact_id),
                        },
                        session=session,
                    )
            result = await self.queue.database.jobs.update_one(
                self.queue._active(job), {"$inc": {"source_epoch": 1}}, session=session
            )
            if not result.matched_count:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "Source worker lease expired.")

        await self.queue.transactions.transaction(save)
