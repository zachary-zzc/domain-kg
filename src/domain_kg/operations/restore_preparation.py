"""Prepare owned inactive target files; no grants or acceptance marker are written."""

from collections.abc import AsyncIterator
from dataclasses import dataclass

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.jobs.models import LeasedJob
from domain_kg.jobs.queue import JobQueue
from domain_kg.materials.models import BlobRef, BlobScope
from domain_kg.models import ScopedRef
from domain_kg.operations.backup import BackupService
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import RestoreCommand
from domain_kg.operations.package_reader import PackageReader
from domain_kg.operations.resealing import RestoreArtifactLedger, RewrittenArtifact
from domain_kg.operations.restore_inventory import MAX_METADATA, RestoreInventory
from domain_kg.operations.restore_stores import RestoreBinding, RestoreStores
from domain_kg.operations.restore_targets import RestoreTargets
from domain_kg.providers.service import ProviderService
from domain_kg.revisions.projections import CurationEmbeddedDocument, EmbeddedDocument
from domain_kg.storage.blobs import BlobStore


@dataclass(frozen=True)
class StagedSource:
    document: EmbeddedDocument


@dataclass(frozen=True)
class PreparedRestore:
    binding: RestoreBinding
    inventory: RestoreInventory
    originals: tuple[BlobRef, ...]
    artifacts: tuple[RewrittenArtifact, ...]
    sources: tuple[StagedSource, ...]
    curations: tuple[CurationEmbeddedDocument, ...]


class RestorePreparation:
    def __init__(self, queue: JobQueue, providers: ProviderService) -> None:
        self.queue, self.providers = queue, providers
        self.bindings = RestoreStores(queue, providers.registry)
        self.targets = RestoreTargets(queue)

    async def _put(
        self,
        job: LeasedJob,
        binding: RestoreBinding,
        reference: BlobRef,
        stream: AsyncIterator[bytes],
    ) -> BlobRef:
        async def guarded() -> AsyncIterator[bytes]:
            async for chunk in stream:
                await self.targets.guard(job)
                yield chunk

        await self.targets.guard(job)
        blobs = BlobStore(binding.stores.blobs)
        try:
            result = await blobs.put(
                guarded(),
                reference.sha256,
                BlobScope(**{key: getattr(reference, key) for key in BlobScope.model_fields}),
                expected_size=reference.size_bytes,
                object_id=reference.object_id,
                media_type=reference.media_type,
            )
        except DomainError as write_error:
            if write_error.code != ErrorCode.BACKEND_UNAVAILABLE:
                raise
            # An immutable object may already exist after replay or a lost
            # response. Only complete checksum-verified bytes establish reuse.
            try:
                async for _ in blobs.open(reference):
                    await self.targets.guard(job)
            except DomainError as read_error:
                if read_error.code == ErrorCode.BACKEND_UNAVAILABLE:
                    raise write_error from None
                raise
            result = reference
        if result != reference:
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Restored object metadata differs."
            )
        await self.targets.guard(job)
        return result

    async def prepare(self, job: LeasedJob) -> PreparedRestore:
        target, _ = await self.bindings.approved(job)
        access = await self.queue.authorize(job)
        operation = await OwnerOperations(self.queue).authorize(
            access, await self.queue._current(job)
        )
        if not isinstance(operation.command, RestoreCommand):
            raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Restore intent differs.")
        _, manifest, store = await BackupService(self.queue)._manifest(
            access, job.kg_id, operation.command.backup_job_id, by_job=True
        )
        reader = PackageReader(store, lambda: self.targets.guard(job))
        inventory = await RestoreInventory.load(reader, manifest)
        mapping = inventory.mapping(target.target)
        documents = await inventory.documents(mapping)
        ledger = RestoreArtifactLedger(
            documents, ScopedRef(org_id=job.org_id, kg_id=job.kg_id, id=job.target_id)
        )
        binding = await self.bindings.provision(job)
        originals = []
        for part in inventory.parts:
            if part.kind == "original" and part.original is not None:
                reference = documents.original(part.original)
                originals.append(await self._put(job, binding, reference, store.open(part.object)))
        artifacts = []
        size = 0
        for artifact, checkpoint, part in inventory.artifacts():
            result = ledger.rewrite(
                artifact, checkpoint, await reader.body(part.object, maximum=50 * 1024 * 1024)
            )
            size += len(result.body)
            if size > MAX_METADATA:
                raise DomainError(
                    ErrorCode.QUOTA_EXCEEDED, "Restore staged artifacts exceed their byte budget."
                )

            async def stream(data: bytes = result.body) -> AsyncIterator[bytes]:
                for start in range(0, len(data), 65536):
                    yield data[start : start + 65536]

            await self._put(job, binding, result.artifact.blob, stream())
            artifacts.append(result)
        sources, curations = [], []
        for part in inventory.parts:
            if part.kind in ("source_projection", "curation_projection"):
                size += part.object.size_bytes
                if size > MAX_METADATA:
                    raise DomainError(
                        ErrorCode.QUOTA_EXCEEDED,
                        "Restore projection documents exceed their byte budget.",
                    )
            if part.kind == "source_projection":
                document = EmbeddedDocument.model_validate_json(
                    await reader.body(part.object, maximum=50 * 1024 * 1024)
                )
                sources.append(
                    StagedSource(documents.embedded(document, binding.embedding, ledger.checkpoint))
                )
            elif part.kind == "curation_projection":
                manual = CurationEmbeddedDocument.model_validate_json(
                    await reader.body(part.object, maximum=50 * 1024 * 1024)
                )
                curations.append(documents.curation(manual, binding.embedding))
        if len(sources) != manifest.source_count or len(curations) != manifest.curation_count:
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Restore accepted owner counts differ."
            )
        if await self.bindings.approved(job) != (binding.target, binding.embedding):
            raise DomainError(ErrorCode.VERSION_CONFLICT, "Restore configuration changed.")
        return PreparedRestore(
            binding, inventory, tuple(originals), tuple(artifacts), tuple(sources), tuple(curations)
        )
