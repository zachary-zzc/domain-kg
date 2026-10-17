"""Per-chunk checkpoints never establish full-document completion or publication."""

import hashlib
from collections.abc import Awaitable, Callable
from uuid import UUID

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.models import DomainSchema
from domain_kg.ingestion.extraction import validate_extraction
from domain_kg.ingestion.models import ExtractionBatch, ParsedChunk
from domain_kg.jobs.models import LeasedJob
from domain_kg.jobs.queue import JobQueue
from domain_kg.storage.blobs import BlobStore


async def cached_extraction(
    queue: JobQueue,
    job: LeasedJob,
    chunk: ParsedChunk,
    schema: DomainSchema,
    profile_id: UUID,
    input_hash: str,
    produce: Callable[[], Awaitable[ExtractionBatch]],
) -> ExtractionBatch:
    access = await queue.authorize(job)
    cache_hash = hashlib.sha256(
        ("chunk-extraction-v1/" + input_hash + "/" + chunk.model_dump_json()).encode()
    ).hexdigest()
    checkpoint = await queue.load_checkpoint(job, "extracting", cache_hash)
    if checkpoint is not None:
        blob = await queue.artifact_blob(job, checkpoint)
        stores = await queue.router.for_kg(access, job.kg_id)
        data = bytearray()
        async for part in BlobStore(stores.blobs).open(blob):
            data.extend(part)
            if len(data) > 2 * 1024**2:
                raise DomainError(ErrorCode.EVIDENCE_VALIDATION_FAILED, "Chunk cache is too large.")
        batch = ExtractionBatch.model_validate_json(data)
    else:
        batch = await produce()
    batch = validate_extraction(chunk, schema, batch)
    if batch.provider_profile_id != profile_id:
        raise DomainError(ErrorCode.EVIDENCE_VALIDATION_FAILED, "Chunk provider differs.")
    await queue.authorize(job)
    if checkpoint is None:
        checkpoint = await queue.store_artifact(
            job, "extracting", cache_hash, batch.model_dump_json().encode()
        )
        await queue.checkpoint(job, checkpoint)
    return batch
