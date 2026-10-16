"""Configured, bounded pipeline steps; an incomplete pipeline never implies publication."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.jobs.models import Checkpoint, LeasedJob, PreparationStage
from domain_kg.jobs.queue import STAGES
from domain_kg.models import SHA256

if TYPE_CHECKING:
    from domain_kg.ingestion.models import ChunkConfig, ParseOptions
    from domain_kg.jobs.queue import JobQueue
    from domain_kg.providers.service import ProviderService


@dataclass(frozen=True)
class PipelineStep:
    stage: PreparationStage
    input_hash: str
    execute: Callable[[LeasedJob], Awaitable[Checkpoint]]
    timeout_seconds: int = 120

    def __post_init__(self) -> None:
        TypeAdapter(SHA256).validate_python(self.input_hash)
        if self.stage not in STAGES or not 1 <= self.timeout_seconds <= 300:
            raise ValueError("invalid bounded pipeline step")


@dataclass(frozen=True)
class Pipeline:
    steps: tuple[PipelineStep, ...] = ()

    def __post_init__(self) -> None:
        order = [STAGES.index(step.stage) for step in self.steps]
        if order != sorted(set(order)):
            raise ValueError("pipeline stages must be unique and ordered")

    @property
    def configured(self) -> bool:
        return tuple(step.stage for step in self.steps) == STAGES

    def require_configured(self) -> None:
        if not self.configured:
            raise DomainError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "Configure all ingestion and publication steps before starting a worker.",
            )


def preparation_steps(
    queue: "JobQueue",
    *,
    options: "ParseOptions | None" = None,
    chunk_config: "ChunkConfig | None" = None,
) -> tuple[PipelineStep, ...]:
    """Per-job source identity is fixed by the durable command and artifact ledger.

    These hashes bind the parser/tokenizer configuration within that exact job.
    They never authorize cross-source or cross-visibility artifact reuse.
    """
    import hashlib
    from importlib.metadata import version

    from domain_kg.ingestion.chunking import VERSION, chunk_document
    from domain_kg.ingestion.models import ChunkConfig, ParsedDocument, ParseOptions
    from domain_kg.ingestion.parsers import Parser
    from domain_kg.ingestion.repository import SourceRepository

    options, chunk_config = options or ParseOptions(), chunk_config or ChunkConfig()
    source = SourceRepository(queue)
    parse_hash = hashlib.sha256(
        (f"native-v1/{version('docling-parse')}/" + options.model_dump_json()).encode()
    ).hexdigest()
    validate_hash = hashlib.sha256(b"source-validation-v1").hexdigest()
    chunk_hash = hashlib.sha256(
        (parse_hash + VERSION + chunk_config.model_dump_json()).encode()
    ).hexdigest()

    async def validate(job: LeasedJob) -> Checkpoint:
        blob, blobs = await source.original(job)
        async for _ in blobs.open(blob):
            pass
        return await queue.store_artifact(
            job, "validating", validate_hash, blob.model_dump_json().encode()
        )

    async def parse(job: LeasedJob) -> Checkpoint:
        blob, blobs = await source.original(job)
        document = await Parser(blobs).parse(blob, options)
        checkpoint = await queue.store_artifact(
            job, "parsing", parse_hash, document.model_dump_json().encode()
        )
        await source.persist(job, document, checkpoint, chunked=False)
        return checkpoint

    async def chunk(job: LeasedJob) -> Checkpoint:
        parsed = await queue.load_checkpoint(job, "parsing", parse_hash)
        if parsed is None:
            raise DomainError(
                ErrorCode.REVISION_NOT_READY, "Parsed source is not ready.", retryable=True
            )
        document = await source.read(job, parsed)
        chunks = chunk_document(document, chunk_config)
        document = ParsedDocument.model_validate(
            document.model_copy(update={"chunks": tuple(chunks)})
        )
        checkpoint = await queue.store_artifact(
            job, "chunking", chunk_hash, document.model_dump_json().encode()
        )
        await source.persist(job, document, checkpoint, chunked=True)
        return checkpoint

    return (
        PipelineStep(stage="validating", input_hash=validate_hash, execute=validate),
        PipelineStep(
            stage="parsing",
            input_hash=parse_hash,
            execute=parse,
            timeout_seconds=options.timeout_seconds,
        ),
        PipelineStep(stage="chunking", input_hash=chunk_hash, execute=chunk),
    )


def extraction_steps(
    queue: "JobQueue", providers: "ProviderService", chunk_step: PipelineStep
) -> tuple[PipelineStep, ...]:
    import hashlib
    from uuid import UUID

    from domain_kg.graph.schema import schema_for_kg
    from domain_kg.ingestion.repository import SourceRepository
    from domain_kg.ingestion.resolution import ResolutionService
    from domain_kg.ingestion.resolved import ExtractionArtifact, ResolvedDocument
    from domain_kg.models import Namespace
    from domain_kg.storage.blobs import BlobStore

    source = SourceRepository(queue)
    configuration = "|".join(
        profile.model_dump_json()
        for _, profile in sorted(providers.registry.profiles.items(), key=lambda pair: str(pair[0]))
    )
    extract_hash = hashlib.sha256(
        (chunk_step.input_hash + configuration + "strict-lightrag-1.5.7-v1").encode()
    ).hexdigest()
    resolve_hash = hashlib.sha256(
        (extract_hash + "conservative-resolution-v1").encode()
    ).hexdigest()

    async def extract(job: LeasedJob) -> Checkpoint:
        checkpoint = await queue.load_checkpoint(job, "chunking", chunk_step.input_hash)
        if checkpoint is None:
            raise DomainError(ErrorCode.REVISION_NOT_READY, "Source chunks are not ready.")
        document = await source.read(job, checkpoint)
        access = await queue.authorize(job)
        selected = await providers.selected(access, job.kg_id)
        profile, _ = providers.registry.resolve(
            UUID(selected["profile_id"]),
            Namespace(org_id=job.org_id, kg_id=job.kg_id),
            restricted=False,
        )
        schema = await schema_for_kg(queue.policy, access, job.kg_id)
        batches = []
        for chunk in document.chunks:
            await queue.authorize(job)
            batches.append(await providers.extract(access, chunk, schema))
        artifact = ExtractionArtifact(
            org_id=job.org_id,
            kg_id=job.kg_id,
            material_id=document.blob.material_id,
            version_id=job.target_id,
            profile_id=profile.id,
            profile_revision=profile.revision,
            selection_revision=selected["revision"],
            domain_schema=schema,
            chunk_artifact_id=checkpoint.artifact_id,
            batches=tuple(batches),
        )
        return await queue.store_artifact(
            job, "extracting", extract_hash, artifact.model_dump_json().encode()
        )

    async def resolve(job: LeasedJob) -> Checkpoint:
        checkpoint = await queue.load_checkpoint(job, "extracting", extract_hash)
        if checkpoint is None:
            raise DomainError(ErrorCode.REVISION_NOT_READY, "Source extraction is not ready.")
        access = await queue.authorize(job)
        stores = await queue.router.for_kg(access, job.kg_id)
        blob = await queue.artifact_blob(job, checkpoint)
        data = bytearray()
        async for part in BlobStore(stores.blobs).open(blob):
            data.extend(part)
        artifact = ExtractionArtifact.model_validate_json(bytes(data))
        original, _ = await source.original(job)
        if (artifact.org_id, artifact.kg_id, artifact.material_id, artifact.version_id) != (
            job.org_id,
            job.kg_id,
            original.material_id,
            job.target_id,
        ):
            raise DomainError(ErrorCode.EVIDENCE_VALIDATION_FAILED, "Extraction source mismatch.")
        selected = await providers.selected(access, job.kg_id)
        if (selected["profile_id"], selected["profile_revision"], selected["revision"]) != (
            str(artifact.profile_id),
            artifact.profile_revision,
            artifact.selection_revision,
        ) or await schema_for_kg(queue.policy, access, job.kg_id) != artifact.domain_schema:
            raise DomainError(ErrorCode.VERSION_CONFLICT, "Extraction configuration changed.")
        service = ResolutionService(queue.policy, queue.router)
        batches = []
        for batch in artifact.batches:
            await queue.authorize(job)
            batches.append(await service.resolve_entities(access, batch))
        resolved = ResolvedDocument(
            org_id=job.org_id,
            kg_id=job.kg_id,
            material_id=original.material_id,
            version_id=job.target_id,
            extraction=artifact,
            batches=tuple(batches),
        )
        return await queue.store_artifact(
            job, "resolving", resolve_hash, resolved.model_dump_json().encode()
        )

    return (
        PipelineStep(
            stage="extracting", input_hash=extract_hash, execute=extract, timeout_seconds=300
        ),
        PipelineStep(stage="resolving", input_hash=resolve_hash, execute=resolve),
    )
