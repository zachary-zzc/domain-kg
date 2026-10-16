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
    from domain_kg.revisions.publication import ProjectionCoordinator


@dataclass(frozen=True)
class PipelineStep:
    stage: PreparationStage
    input_hash: str
    execute: Callable[[LeasedJob], Awaitable[Checkpoint]]
    timeout_seconds: int = 120
    input_hash_for: Callable[[LeasedJob], Awaitable[str]] | None = None

    def __post_init__(self) -> None:
        TypeAdapter(SHA256).validate_python(self.input_hash)
        if self.stage not in STAGES or not 1 <= self.timeout_seconds <= 300:
            raise ValueError("invalid bounded pipeline step")

    async def hash_for(self, job: LeasedJob) -> str:
        value = await self.input_hash_for(job) if self.input_hash_for else self.input_hash
        return str(TypeAdapter(SHA256).validate_python(value))


@dataclass(frozen=True)
class Pipeline:
    steps: tuple[PipelineStep, ...] = ()
    repair_steps: tuple[PipelineStep, ...] = ()
    restore_steps: tuple[PipelineStep, ...] = ()

    def __post_init__(self) -> None:
        order = [STAGES.index(step.stage) for step in self.steps]
        if order != sorted(set(order)):
            raise ValueError("pipeline stages must be unique and ordered")
        for steps in (self.repair_steps, self.restore_steps):
            if steps and tuple(step.stage for step in steps) != (
                "embedding",
                "projecting",
                "verifying",
                "publishing",
            ):
                raise ValueError(
                    "retained-data work uses only immutable artifact/publication stages"
                )

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
    providers: "ProviderService | None" = None,
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

    async def current_chunk_hash(job: LeasedJob) -> str:
        if providers is None:
            return chunk_hash
        from uuid import UUID

        from domain_kg.graph.schema import schema_for_kg
        from domain_kg.models import Namespace

        access = await queue.authorize(job)
        selected = await providers.selected(access, job.kg_id)
        profile, _ = providers.registry.resolve(
            UUID(selected["profile_id"]),
            Namespace(org_id=job.org_id, kg_id=job.kg_id),
            restricted=False,
        )
        schema = await schema_for_kg(queue.policy, access, job.kg_id)
        return hashlib.sha256(
            (
                chunk_hash
                + "/model-window-v1/"
                + profile.model_dump_json()
                + schema.model_dump_json()
                + str(selected["revision"])
            ).encode()
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
        wanted_hash = await current_chunk_hash(job)
        parsed = await queue.load_checkpoint(job, "parsing", parse_hash)
        if parsed is None:
            raise DomainError(
                ErrorCode.REVISION_NOT_READY, "Parsed source is not ready.", retryable=True
            )
        document = await source.read(job, parsed)
        chunks = chunk_document(document, chunk_config)
        if providers is not None:
            from uuid import UUID

            from domain_kg.graph.schema import schema_for_kg
            from domain_kg.models import Namespace
            from domain_kg.providers.budget import fit_source_chunks

            access = await queue.authorize(job)
            selected = await providers.selected(access, job.kg_id)
            profile, _ = providers.registry.resolve(
                UUID(selected["profile_id"]),
                Namespace(org_id=job.org_id, kg_id=job.kg_id),
                restricted=False,
            )
            schema = await schema_for_kg(queue.policy, access, job.kg_id)
            chunks = fit_source_chunks(chunks, schema, profile)
            if await current_chunk_hash(job) != wanted_hash:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "Model window configuration changed.")
        document = ParsedDocument.model_validate(
            document.model_copy(update={"chunks": tuple(chunks)})
        )
        checkpoint = await queue.store_artifact(
            job, "chunking", wanted_hash, document.model_dump_json().encode()
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
        PipelineStep(
            stage="chunking",
            input_hash=chunk_hash,
            execute=chunk,
            input_hash_for=current_chunk_hash,
        ),
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

    async def current_extract_hash(job: LeasedJob) -> str:
        access = await queue.authorize(job)
        selected = await providers.selected(access, job.kg_id)
        schema = await schema_for_kg(queue.policy, access, job.kg_id)
        return hashlib.sha256(
            (
                extract_hash
                + "/"
                + str(selected["profile_id"])
                + "/"
                + str(selected["profile_revision"])
                + "/"
                + str(selected["revision"])
                + "/"
                + schema.model_dump_json()
            ).encode()
        ).hexdigest()

    async def current_resolve_hash(job: LeasedJob) -> str:
        return hashlib.sha256(
            ((await current_extract_hash(job)) + resolve_hash).encode()
        ).hexdigest()

    async def extract(job: LeasedJob) -> Checkpoint:
        wanted_hash = await current_extract_hash(job)
        checkpoint = await queue.load_checkpoint(job, "chunking", await chunk_step.hash_for(job))
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
        if await current_extract_hash(job) != wanted_hash:
            raise DomainError(ErrorCode.VERSION_CONFLICT, "Extraction configuration changed.")
        return await queue.store_artifact(
            job, "extracting", wanted_hash, artifact.model_dump_json().encode()
        )

    async def resolve(job: LeasedJob) -> Checkpoint:
        wanted_hash = await current_resolve_hash(job)
        checkpoint = await queue.load_checkpoint(job, "extracting", await current_extract_hash(job))
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
        if await current_resolve_hash(job) != wanted_hash:
            raise DomainError(ErrorCode.VERSION_CONFLICT, "Extraction configuration changed.")
        return await queue.store_artifact(
            job, "resolving", wanted_hash, resolved.model_dump_json().encode()
        )

    return (
        PipelineStep(
            stage="extracting",
            input_hash=extract_hash,
            execute=extract,
            timeout_seconds=300,
            input_hash_for=current_extract_hash,
        ),
        PipelineStep(
            stage="resolving",
            input_hash=resolve_hash,
            execute=resolve,
            input_hash_for=current_resolve_hash,
        ),
    )


def publication_steps(
    queue: "JobQueue",
    coordinator: "ProjectionCoordinator",
    resolve_step: PipelineStep,
    *,
    repair: bool = False,
    restoration: bool = False,
) -> tuple[PipelineStep, ...]:
    import hashlib

    from domain_kg.models import SourceVersion
    from domain_kg.revisions.journal import read_artifact
    from domain_kg.revisions.models import ProjectionReceipt, PublishRevision, ReadinessReport

    def root(stage: str) -> str:
        return hashlib.sha256((resolve_step.input_hash + "/" + stage + "-v1").encode()).hexdigest()

    async def current_hash(job: LeasedJob, stage: str) -> str:
        if repair or restoration:
            raw = await queue._current(job)
            return hashlib.sha256(
                (
                    str(job.id)
                    + str(raw["repair_parent_manifest_id"])
                    + ("/restore-material/" if restoration else "/repair/")
                    + stage
                    + "-v1"
                ).encode()
            ).hexdigest()
        return hashlib.sha256(
            ((await resolve_step.hash_for(job)) + "/" + stage + "-v1").encode()
        ).hexdigest()

    async def required(job: LeasedJob, step: PipelineStep) -> Checkpoint:
        checkpoint = await queue.load_checkpoint(job, step.stage, await step.hash_for(job))
        if checkpoint is None:
            raise DomainError(
                ErrorCode.REVISION_NOT_READY,
                "The prior pipeline stage is not ready.",
                retryable=True,
            )
        return checkpoint

    async def data(job: LeasedJob, checkpoint: Checkpoint) -> bytes:
        access = await queue.authorize(job)
        raw = await queue._current(job)
        source = SourceVersion(
            org_id=job.org_id,
            kg_id=job.kg_id,
            material_id=raw["material_id"],
            version_id=job.target_id,
        )
        from domain_kg.materials.restoration import restoration_permit

        return await read_artifact(
            queue,
            access,
            checkpoint,
            source,
            restoration=await restoration_permit(queue, access, job),
        )

    async def embed_hash(job: LeasedJob) -> str:
        return await current_hash(job, "embedding")

    async def embed(job: LeasedJob) -> Checkpoint:
        if restoration:
            from domain_kg.materials.restoration import restore_embedding_artifact

            return await restore_embedding_artifact(queue, job, input_hash=await embed_hash(job))
        if repair:
            from domain_kg.revisions.repair import RepairService

            return await RepairService(queue).embed(job, input_hash=await embed_hash(job))
        return await coordinator.embed(
            job, await required(job, resolve_step), input_hash=await embed_hash(job)
        )

    embedding = PipelineStep(
        stage="embedding",
        input_hash=root("embedding"),
        execute=embed,
        input_hash_for=embed_hash,
        timeout_seconds=300,
    )

    async def project_hash(job: LeasedJob) -> str:
        return await current_hash(job, "projecting")

    async def project(job: LeasedJob) -> Checkpoint:
        batch = await coordinator.prepare(job, await required(job, embedding))
        receipt = await coordinator.stage(await queue.authorize(job), batch)
        return await queue.store_artifact(
            job, "projecting", await project_hash(job), receipt.model_dump_json().encode()
        )

    projecting = PipelineStep(
        stage="projecting",
        input_hash=root("projecting"),
        execute=project,
        input_hash_for=project_hash,
    )

    async def verify_hash(job: LeasedJob) -> str:
        return await current_hash(job, "verifying")

    async def verify(job: LeasedJob) -> Checkpoint:
        receipt = ProjectionReceipt.model_validate_json(
            await data(job, await required(job, projecting))
        )
        report = await coordinator.verify(await queue.authorize(job), receipt)
        return await queue.store_artifact(
            job, "verifying", await verify_hash(job), report.model_dump_json().encode()
        )

    verifying = PipelineStep(
        stage="verifying", input_hash=root("verifying"), execute=verify, input_hash_for=verify_hash
    )

    async def publish_hash(job: LeasedJob) -> str:
        return await current_hash(job, "publishing")

    async def publish(job: LeasedJob) -> Checkpoint:
        report = ReadinessReport.model_validate_json(
            await data(job, await required(job, verifying))
        )
        lease = await queue.acquire_publication(job)
        command = PublishRevision(
            report=report,
            lease_token=lease.lease_token,
            fence=lease.fence,
            expected_revision=report.batch.expected_revision,
            idempotency_key=f"publish-{job.id}-{job.attempt_id}",
        )
        checkpoint = await queue.store_artifact(
            job, "publishing", await publish_hash(job), command.model_dump_json().encode()
        )
        await coordinator.publish(await queue.authorize(job), command)
        return checkpoint

    publishing = PipelineStep(
        stage="publishing",
        input_hash=root("publishing"),
        execute=publish,
        input_hash_for=publish_hash,
    )
    return embedding, projecting, verifying, publishing


def configured_pipeline(queue: "JobQueue", providers: "ProviderService") -> Pipeline:
    from domain_kg.revisions.publication import ProjectionCoordinator

    preparation = preparation_steps(queue, providers=providers)
    extraction = extraction_steps(queue, providers, preparation[-1])
    publication = publication_steps(queue, ProjectionCoordinator(queue, providers), extraction[-1])
    repair = publication_steps(
        queue, ProjectionCoordinator(queue, providers), extraction[-1], repair=True
    )
    restore = publication_steps(
        queue, ProjectionCoordinator(queue, providers), extraction[-1], restoration=True
    )
    return Pipeline(
        preparation + extraction + publication, repair_steps=repair, restore_steps=restore
    )
