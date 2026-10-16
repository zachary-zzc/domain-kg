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
