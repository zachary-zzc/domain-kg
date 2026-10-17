"""Verified retained backup packages with one Mongo acceptance boundary."""

import asyncio
import hashlib
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import ValidationError
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext, Action, Principal
from domain_kg.imports.provenance import check_import_origin
from domain_kg.jobs.models import Checkpoint, JobArtifact, JobCommand, LeasedJob
from domain_kg.jobs.operations import OperationArtifact
from domain_kg.jobs.queue import JobQueue, now
from domain_kg.materials.models import BlobRef
from domain_kg.models import ScopedRef, SourceVersion
from domain_kg.operations.authority import AuthorityCapture
from domain_kg.operations.backup_models import BackupManifest, BackupSeal
from domain_kg.operations.capture import BackupCapture
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import BackupCommand, OwnerOperation
from domain_kg.operations.package_reader import PackageReader
from domain_kg.operations.packages import PackageObject, PackagePage, PackagePart
from domain_kg.operations.restored_attempts import restored_job
from domain_kg.operations.storage import PackageStore
from domain_kg.revisions.curation_publication import directive
from domain_kg.revisions.journal import PublicationJournal, read_artifact
from domain_kg.revisions.models import ProjectionReceipt, ReadinessReport, RevisionBatch
from domain_kg.revisions.projections import (
    AcceptedProjection,
    CurationEmbeddedDocument,
    EmbeddedDocument,
)
from domain_kg.storage.blobs import BlobStore
from domain_kg.storage.router import SourceStores


@dataclass
class _Capture:
    job: LeasedJob
    operation: OwnerOperation
    stores: SourceStores
    packages: PackageStore
    copied_bytes: int = 0
    object_count: int = 0


class BackupService:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database = queue, queue.database
        self.capture, self.operations = BackupCapture(queue), OwnerOperations(queue)

    async def _initialize(self) -> None:
        for table in ("backup_results", "backup_objects"):
            await self.database[table].create_index(
                [("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True
            )

    async def _object(
        self, context: _Capture, body: bytes, media_type: str = "application/json"
    ) -> PackageObject:
        reference = PackageObject(
            org_id=context.job.org_id,
            kg_id=context.job.kg_id,
            backup_id=context.job.target_id,
            attempt_id=context.job.attempt_id,
            id=uuid4(),
            sha256=hashlib.sha256(body).hexdigest(),
            size_bytes=len(body),
            media_type=media_type,
        )

        async def chunks() -> AsyncIterator[bytes]:
            for offset in range(0, len(body), 65536):
                await self.capture.guard(context.job)
                yield body[offset : offset + 65536]

        await self._store(context, reference, chunks())
        return reference

    async def _store(
        self, context: _Capture, reference: PackageObject, chunks: AsyncIterable[bytes]
    ) -> None:
        context.copied_bytes += reference.size_bytes
        context.object_count += 1
        if context.copied_bytes > 2 * 1024**3 or context.object_count > 100000:
            raise DomainError(
                ErrorCode.QUOTA_EXCEEDED, "Backup package exceeds its capture budget."
            )
        await self.capture.guard(context.job)
        await context.packages.put(reference, chunks)
        async for _ in context.packages.open(reference):
            await self.capture.guard(context.job)
        await self.database.backup_objects.insert_one(
            reference.model_dump(mode="json")
            | {"job_id": str(context.job.id), "created_at": now(), "state": "staged"}
        )

    async def _copy_blob(
        self, context: _Capture, blob: BlobRef, kind: Literal["original", "source_artifact"]
    ) -> PackagePart:
        access = await self.capture.guard(context.job)
        source = SourceVersion(**{key: getattr(blob, key) for key in SourceVersion.model_fields})
        if (source.org_id, source.kg_id) != (context.job.org_id, context.job.kg_id):
            raise inaccessible_reference()
        version = await context.stores.authority.get_material_version(
            ScopedRef(org_id=source.org_id, kg_id=source.kg_id, id=source.version_id)
        )
        if version is None or version.material.id != source.material_id:
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Backup blob source ownership differs."
            )
        reference = PackageObject(
            org_id=blob.org_id,
            kg_id=blob.kg_id,
            backup_id=context.job.target_id,
            attempt_id=context.job.attempt_id,
            id=uuid4(),
            sha256=blob.sha256,
            size_bytes=blob.size_bytes,
            media_type=blob.media_type,
        )

        async def chunks() -> AsyncIterator[bytes]:
            async for part in BlobStore(context.stores.blobs).open(blob):
                await self.capture.guard(context.job)
                yield part

        await self.queue.policy.revalidate(access)
        await self._store(context, reference, chunks())
        return PackagePart(
            object=reference,
            kind=kind,
            source=source,
            original=blob if kind == "original" else None,
            artifact_blob=blob if kind == "source_artifact" else None,
        )

    async def _projection(self, context: _Capture, binding: AcceptedProjection) -> PackagePart:
        access = await self.capture.guard(context.job)
        attempt = await PublicationJournal(self.queue).attempt(access, binding.manifest_id)
        batch = RevisionBatch.model_validate(attempt["batch"])
        ReadinessReport(
            batch=batch,
            receipts=tuple(ProjectionReceipt.model_validate(row) for row in attempt["receipts"]),
            ready=True,
        )
        origin = binding.import_origin.model_dump(mode="json") if binding.import_origin else None
        if (
            attempt["state"] != "accepted"
            or any(
                getattr(batch, field) != getattr(binding, field)
                for field in (
                    "org_id",
                    "kg_id",
                    "attempt_id",
                    "manifest_id",
                    "generation",
                    "content_hash",
                )
            )
            or attempt.get("source")
            != (binding.source.model_dump(mode="json") if binding.source else None)
            or attempt.get("curation")
            != (binding.curation.model_dump(mode="json") if binding.curation else None)
            or attempt.get("import_origin") != origin
        ):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Backup accepted projection ownership differs."
            )
        checkpoint = Checkpoint.model_validate(attempt["embedded_checkpoint"])
        origin_job: JobCommand
        if attempt.get("generation_origin"):
            from domain_kg.operations.generation_results import generation_artifact_job

            origin_job = await generation_artifact_job(self.queue, access, attempt)
        elif attempt.get("maintenance_origin"):
            from domain_kg.operations.maintenance_results import maintenance_artifact_job

            origin_job = await maintenance_artifact_job(self.queue, access, attempt)
        elif attempt.get("restored_origin"):
            origin_job = await restored_job(self.queue, access, attempt)
        else:
            leased_origin = LeasedJob.model_validate(attempt["job"])
            if leased_origin.attempt_id != binding.attempt_id:
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Backup artifact attempt differs."
                )
            origin_job = leased_origin
        if (checkpoint.org_id, checkpoint.kg_id, checkpoint.job_id, checkpoint.stage) != (
            binding.org_id,
            binding.kg_id,
            origin_job.id,
            "embedding",
        ) or (origin_job.org_id, origin_job.kg_id) != (
            binding.org_id,
            binding.kg_id,
        ):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Backup artifact job ownership differs."
            )
        if binding.source is not None:
            if origin_job.target_id != binding.source.version_id or origin_job.kind == "curate":
                raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Backup source job differs.")
            await check_import_origin(self.database, binding.source, binding.import_origin)
            permit = await self.capture.source_permit(context.job, binding.source)
            body = await read_artifact(
                self.queue, access, checkpoint, binding.source, backup=permit
            )
            document = EmbeddedDocument.model_validate_json(body)
            if (
                SourceVersion(**{key: getattr(document, key) for key in SourceVersion.model_fields})
                != binding.source
                or document.import_origin != binding.import_origin
                or document.chunk_checkpoint.artifact_id != binding.chunk_artifact_id
            ):
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Backup projection source differs."
                )
            part_kind: Literal["source_projection", "curation_projection"] = "source_projection"
        else:
            owner = binding.curation
            if owner is None:
                raise inaccessible_reference()
            if origin_job.kind != "curate" or origin_job.target_id != owner.id:
                raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Backup curation job differs.")
            record = await self.database.curation_revisions.find_one(
                {
                    "org_id": str(context.job.org_id),
                    "kg_id": str(context.job.kg_id),
                    "id": str(owner.id),
                    "state": "accepted",
                }
            )
            artifact = await self.database.operation_artifacts.find_one(
                {
                    "org_id": str(context.job.org_id),
                    "kg_id": str(context.job.kg_id),
                    "id": str(checkpoint.artifact_id),
                    "owner": owner.model_dump(mode="json"),
                    "job_id": str(checkpoint.job_id),
                    "stage": checkpoint.stage,
                    "input_hash": checkpoint.input_hash,
                }
            )
            if record is None or artifact is None:
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Backup curation artifact is missing."
                )
            typed = OperationArtifact.model_validate(
                {key: value for key, value in artifact.items() if key != "_id"}
            )
            body = typed.body.encode()
            curation_document = CurationEmbeddedDocument.model_validate_json(body)
            if (
                typed.output_hash != checkpoint.output_hash
                or curation_document.curation != owner
                or curation_document.directive != directive(record)
                or record.get("job_id") != str(origin_job.id)
            ):
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Backup curation ownership differs."
                )
            part_kind = "curation_projection"
        if hashlib.sha256(body).hexdigest() != binding.content_hash:
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Backup accepted artifact checksum differs."
            )
        copied = await self._object(context, body)
        return PackagePart(
            object=copied, kind=part_kind, source=binding.source, curation=binding.curation
        )

    async def run(self, job: LeasedJob) -> None:
        if job.kind != "backup":
            raise inaccessible_reference()
        await self._initialize()
        try:
            async with asyncio.timeout(300):
                await self._run(job)
        except (ValidationError, ValueError, KeyError, TypeError):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT,
                "The backup authority or package is inconsistent.",
            ) from None
        finally:
            await self.capture.release(job)

    async def _run(self, job: LeasedJob) -> None:
        view = await self.capture.start(job)
        access = await self.capture.guard(job)
        raw = await self.queue._current(job)
        operation = await self.operations.authorize(access, raw)
        if not isinstance(operation.command, BackupCommand):
            raise inaccessible_reference()
        stores = await self.queue.router.for_sources(access, job.kg_id)
        context = _Capture(job, operation, stores, PackageStore(stores.blobs))
        authority = AuthorityCapture(self.queue, job, view)
        selected = await authority.selection()
        parts: list[PackagePart] = []
        async for section in authority.sections(selected):
            reference = await self._object(context, section.model_dump_json().encode())
            parts.append(PackagePart(object=reference, kind="authority"))
        ns = {"org_id": str(job.org_id), "kg_id": str(job.kg_id)}
        async for row in stores.authority._database.version_blobs.find(
            ns
            | {
                "id": {"$in": list(selected.version_ids)},
                "state": {"$in": ["accepted", "retained"]},
            }
        ):
            blob = BlobRef.model_validate(row["blob"])
            if str(blob.version_id) != row["id"] or str(blob.material_id) != row["material_id"]:
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Backup original owner differs."
                )
            parts.append(await self._copy_blob(context, blob, "original"))
        async for row in self.database.job_artifacts.find(
            ns | {"job_id": {"$in": list(selected.job_ids)}}
        ):
            artifact = JobArtifact.model_validate(
                {key: value for key, value in row.items() if key != "_id"}
            )
            if str(artifact.source_version_id) not in selected.version_ids:
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Backup artifact version differs."
                )
            parts.append(await self._copy_blob(context, artifact.blob, "source_artifact"))
        for binding in view.projections:
            parts.append(await self._projection(context, binding))
        originals = {part.source for part in parts if part.kind == "original"}
        if any(source not in originals for source in view.sources):
            raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "An accepted original is missing.")
        pages = []
        for index, offset in enumerate(range(0, len(parts), 100)):
            page = PackagePage(
                org_id=job.org_id,
                kg_id=job.kg_id,
                backup_id=job.target_id,
                attempt_id=job.attempt_id,
                index=index,
                parts=tuple(parts[offset : offset + 100]),
            )
            pages.append(await self._object(context, page.model_dump_json().encode()))
        created = now()
        manifest = BackupManifest(
            org_id=job.org_id,
            kg_id=job.kg_id,
            backup_id=job.target_id,
            job_id=job.id,
            attempt_id=job.attempt_id,
            snapshot=operation.snapshot,
            created_at=created,
            expires_at=created + timedelta(days=operation.command.retention_days),
            source_count=len(view.sources),
            curation_count=len(view.curations),
            part_count=len(parts),
            pages=tuple(pages),
        )
        encoded = manifest.model_dump_json().encode()
        if len(encoded) > 1024 * 1024:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Backup manifest exceeds its byte budget.")
        header = await self._object(context, encoded)
        await self._seal(
            context,
            BackupSeal(
                manifest=header,
                snapshot=operation.snapshot,
                job_id=job.id,
                created_at=created,
                expires_at=manifest.expires_at,
            ),
        )

    async def _seal(self, context: _Capture, seal: BackupSeal) -> None:
        job = context.job
        await self.capture.guard(job)
        reader = PackageReader(context.packages, lambda: self.capture.guard(job))
        manifest = BackupManifest.model_validate_json(await reader.body(seal.manifest))
        if (manifest.backup_id, manifest.job_id, manifest.attempt_id, manifest.snapshot) != (
            job.target_id,
            job.id,
            job.attempt_id,
            context.operation.snapshot,
        ):
            raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Backup seal ownership differs.")
        await reader.verify(manifest)

        async def accept(session: AsyncClientSession) -> None:
            access = await self.queue.authorize(job, session)
            await self.queue.transactions.fence(access, access.action, session)
            pin = await self.capture._pin(job, session)
            if pin.snapshot != seal.snapshot or pin.policy_revision != access.policy_revision:
                raise DomainError(ErrorCode.POLICY_CHANGED, "Backup capture authority changed.")
            await self.capture._boundary(job, pin.snapshot, session)
            row = seal.model_dump(mode="json") | {
                "org_id": str(job.org_id),
                "kg_id": str(job.kg_id),
                "id": str(job.target_id),
                "state": "sealed",
            }
            await self.database.backup_results.insert_one(row, session=session)
            changed = await self.database.jobs.update_one(
                self.queue._active(job),
                {
                    "$set": {
                        "stage": "ready",
                        "progress": 1,
                        "lease_until": now(),
                        "error_code": None,
                        "accepted_operation_id": str(job.target_id),
                        "accepted_operation_hash": seal.manifest.sha256,
                    }
                },
                session=session,
            )
            if not changed.matched_count:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The backup lease changed.")
            await self.database.kgs.update_one(
                {"org_id": str(job.org_id), "id": str(job.kg_id)},
                {"$inc": {"backup_capture_epoch": 1}},
                session=session,
            )
            await self.operations.audit.append(
                access, job.kg_id, job.target_id, job.id, "backup", "completed", session
            )

        await self.queue.transactions.transaction(accept)

    async def _sealed(
        self, access: AccessContext, kg: UUID, identifier: UUID, *, by_job: bool
    ) -> tuple[AccessContext, dict[str, Any], BackupSeal, PackageStore]:
        current = await self.queue.policy.revalidate(access, action=Action.BACKUP)
        if kg not in current.kg_ids:
            raise inaccessible_reference()
        ns = {"org_id": str(current.org_id), "kg_id": str(kg)}
        query = {"id": str(identifier)} if by_job else {"target_id": str(identifier)}
        job = await self.database.jobs.find_one(ns | query | {"kind": "backup", "stage": "ready"})
        if job is None:
            raise DomainError(ErrorCode.REVISION_NOT_READY, "No completed backup is available.")
        operation = await self.operations.authorize(current, job)
        row = await self.database.backup_results.find_one(
            ns | {"id": str(operation.id), "state": "sealed"}
        )
        try:
            if row is None:
                raise ValueError("missing seal")
            seal = BackupSeal.model_validate({key: row[key] for key in BackupSeal.model_fields})
            if (
                seal.manifest.org_id,
                seal.manifest.kg_id,
                seal.manifest.backup_id,
                seal.manifest.attempt_id,
                seal.job_id,
                seal.snapshot,
                seal.manifest.sha256,
            ) != (
                current.org_id,
                kg,
                operation.id,
                UUID(job["attempt_id"]),
                UUID(job["id"]),
                operation.snapshot,
                job.get("accepted_operation_hash"),
            ) or job.get("accepted_operation_id") != str(operation.id):
                raise ValueError("seal ownership differs")
        except (ValidationError, ValueError, KeyError, TypeError):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "The backup seal is inconsistent."
            ) from None
        if seal.expires_at <= now():
            raise DomainError(
                ErrorCode.RESOURCE_NOT_ACCESSIBLE, "The backup retention has expired."
            )
        store = PackageStore((await self.queue.router.for_sources(current, kg)).blobs)
        return current, job, seal, store

    async def _bytes(self, store: PackageStore, reference: PackageObject, *, maximum: int) -> bytes:
        if reference.size_bytes > maximum:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Backup metadata exceeds its read budget.")
        return b"".join([chunk async for chunk in store.open(reference)])

    async def _manifest(
        self, access: AccessContext, kg: UUID, identifier: UUID, *, by_job: bool
    ) -> tuple[AccessContext, BackupManifest, PackageStore]:
        current, job, seal, store = await self._sealed(access, kg, identifier, by_job=by_job)
        try:
            manifest = BackupManifest.model_validate_json(
                await self._bytes(store, seal.manifest, maximum=1024 * 1024)
            )
            if (
                manifest.org_id,
                manifest.kg_id,
                manifest.backup_id,
                manifest.job_id,
                manifest.attempt_id,
                manifest.snapshot,
                manifest.expires_at,
            ) != (
                current.org_id,
                kg,
                seal.manifest.backup_id,
                seal.job_id,
                UUID(job["attempt_id"]),
                seal.snapshot,
                seal.expires_at,
            ):
                raise ValueError("manifest ownership differs")
        except (ValidationError, ValueError, KeyError, TypeError):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "The backup manifest is invalid."
            ) from None
        await self.queue.policy.revalidate(current, action=Action.BACKUP)
        return current, manifest, store

    async def manifest(self, access: AccessContext, kg: UUID, job_id: UUID) -> BackupManifest:
        _, manifest, _ = await self._manifest(access, kg, job_id, by_job=True)
        return manifest

    async def parts(
        self, access: AccessContext, kg: UUID, backup_id: UUID, index: int
    ) -> PackagePage:
        current, manifest, store = await self._manifest(access, kg, backup_id, by_job=False)
        if index < 0 or index >= len(manifest.pages):
            raise inaccessible_reference()
        try:
            page = PackagePage.model_validate_json(
                await self._bytes(store, manifest.pages[index], maximum=1024 * 1024)
            )
            if (page.org_id, page.kg_id, page.backup_id, page.attempt_id, page.index) != (
                manifest.org_id,
                manifest.kg_id,
                manifest.backup_id,
                manifest.attempt_id,
                index,
            ):
                raise ValueError("page ownership differs")
        except (ValidationError, ValueError, TypeError):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "The backup page is inconsistent."
            ) from None
        await self.queue.policy.revalidate(current, action=Action.BACKUP)
        return page

    async def verify(self, access: AccessContext, kg: UUID, backup_id: UUID) -> BackupManifest:
        current, manifest, store = await self._manifest(access, kg, backup_id, by_job=False)
        await PackageReader(
            store, lambda: self.queue.policy.revalidate(current, action=Action.BACKUP)
        ).verify(manifest)
        await self.queue.policy.revalidate(current, action=Action.BACKUP)
        return manifest

    async def accepted(self, job: LeasedJob) -> bool:
        row = await self.database.jobs.find_one(
            self.queue._scope(job)
            | {
                "kind": "backup",
                "stage": "ready",
                "attempt_id": str(job.attempt_id),
                "lease_token": str(job.lease_token),
                "fence": job.fence,
            }
        )
        if row is None:
            return False
        access = await self.queue.policy.resolve_access(
            Principal.model_validate(row["actor"]), [job.kg_id], Action.BACKUP, org_id=job.org_id
        )
        manifest = await self.manifest(access, job.kg_id, job.id)
        return manifest.backup_id == job.target_id and manifest.attempt_id == job.attempt_id
