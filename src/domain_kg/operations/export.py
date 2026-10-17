"""Current scoped exports use verified graph views and one fenced Mongo seal."""

import asyncio
import hashlib
from collections.abc import AsyncGenerator, AsyncIterator
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import AccessContext, Action, Principal
from domain_kg.jobs.models import LeasedJob
from domain_kg.jobs.queue import JobQueue, now
from domain_kg.materials.service import digest
from domain_kg.operations.export_formats import encode_records
from domain_kg.operations.export_models import ExportManifest, ExportObject, ExportStore
from domain_kg.operations.export_views import export_views
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import ExportCommand, OwnerOperation, SnapshotPointer
from domain_kg.retrieval.models import SearchRequest
from domain_kg.revisions.models import ReadScope


class ExportService:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database = queue, queue.database
        self.operations, self.graph = OwnerOperations(queue), GraphService(queue)

    async def _boundary(
        self, pointer: SnapshotPointer, session: AsyncClientSession | None = None
    ) -> None:
        row = await self.database.kgs.find_one(
            {"org_id": str(pointer.org_id), "id": str(pointer.kg_id)}, session=session
        )
        if row is None or (
            row.get("published_revision"),
            row.get("generation"),
            row.get("published_manifest_id"),
            row.get("visibility_revision"),
            row.get("revision", 0),
            row.get("configuration_epoch", 0),
        ) != (
            pointer.revision,
            str(pointer.generation),
            str(pointer.manifest_id) if pointer.manifest_id else None,
            pointer.visibility_revision,
            pointer.metadata_revision,
            pointer.configuration_epoch,
        ):
            raise DomainError(ErrorCode.POLICY_CHANGED, "The export read boundary changed.")

    async def _scope(self, access: AccessContext, operation: OwnerOperation) -> ReadScope:
        if not isinstance(operation.command, ExportCommand):
            raise inaccessible_reference()
        return await self.graph.resolver.resolve(
            access,
            SearchRequest(
                kg_ids=(operation.kg_id,),
                query="export",
                mode="graph",
                filters=operation.command.filters,
            ),
            operation.kg_id,
        )

    async def _guard(
        self, job: LeasedJob, operation: OwnerOperation, scope: ReadScope
    ) -> AccessContext:
        current = await self.queue.authorize(job)
        await self.graph.resolver.revalidate(scope)
        await self._boundary(operation.snapshot)
        return current

    async def run(self, job: LeasedJob) -> None:
        if job.kind != "export":
            raise inaccessible_reference()
        try:
            async with asyncio.timeout(300):
                await self._run(job)
        except (ValidationError, ValueError, KeyError, TypeError):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "The export evidence is inconsistent."
            ) from None

    async def _run(self, job: LeasedJob) -> None:
        access = await self.queue.authorize(job)
        operation = await self.operations.authorize(access, await self.queue._current(job))
        command = operation.command
        if not isinstance(command, ExportCommand):
            raise inaccessible_reference()
        scope = await self._scope(access, operation)
        await self._guard(job, operation, scope)
        data = await self.graph.repository.open(scope)
        nodes, edges = export_views(data, self.graph.repository)
        body, media = encode_records(scope.view, nodes, edges, command.format)
        if not body:
            body = b"\n"  # A valid empty JSONL file; retained objects have a nonzero length.
        created = now()
        reference = ExportObject(
            org_id=job.org_id,
            kg_id=job.kg_id,
            id=uuid4(),
            export_id=job.target_id,
            attempt_id=job.attempt_id,
            sha256=hashlib.sha256(body).hexdigest(),
            size_bytes=len(body),
            media_type=media,
        )
        store = ExportStore((await self.queue.router.for_sources(access, job.kg_id)).blobs)

        async def chunks() -> AsyncIterator[bytes]:
            for offset in range(0, len(body), 65536):
                await self._guard(job, operation, scope)
                yield body[offset : offset + 65536]

        await store.put(reference.retained, chunks())
        async for _ in store.open(reference.retained):
            await self._guard(job, operation, scope)
        manifest = ExportManifest(
            org_id=job.org_id,
            kg_id=job.kg_id,
            export_id=job.target_id,
            job_id=job.id,
            attempt_id=job.attempt_id,
            snapshot=operation.snapshot,
            policy_revision=scope.access.policy_revision,
            filters_hash=digest(command.filters.model_dump(mode="json")),
            format=command.format,
            node_count=len(nodes),
            edge_count=len(edges),
            created_at=created,
            expires_at=created + timedelta(days=1),
            content=reference,
        )
        await self._seal(job, operation, scope, manifest)

    async def _seal(
        self, job: LeasedJob, operation: OwnerOperation, scope: ReadScope, manifest: ExportManifest
    ) -> None:
        await self._guard(job, operation, scope)
        await self.database.export_results.create_index(
            [("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True
        )

        async def commit(session: AsyncClientSession) -> None:
            current = await self.queue.authorize(job, session)
            await self.queue.transactions.fence(current, Action.CONFIGURE, session)
            if current.policy_revision != manifest.policy_revision:
                raise DomainError(ErrorCode.POLICY_CHANGED, "The export policy changed.")
            await self._boundary(operation.snapshot, session)
            await self.database.export_results.insert_one(
                manifest.model_dump(mode="json") | {"id": str(operation.id), "state": "sealed"},
                session=session,
            )
            changed = await self.database.jobs.update_one(
                self.queue._active(job),
                {
                    "$set": {
                        "stage": "ready",
                        "progress": 1,
                        "lease_until": now(),
                        "error_code": None,
                        "accepted_operation_id": str(operation.id),
                        "accepted_operation_hash": digest(manifest.model_dump(mode="json")),
                    }
                },
                session=session,
            )
            if not changed.matched_count:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The export lease changed.")
            await self.database.kgs.update_one(
                {"org_id": str(job.org_id), "id": str(job.kg_id)},
                {"$inc": {"owner_operation_epoch": 1}},
                session=session,
            )
            await self.operations.audit.append(
                current, job.kg_id, operation.id, job.id, "export", "completed", session
            )

        await self.queue.transactions.transaction(commit)

    async def _manifest(
        self, access: AccessContext, kg: UUID, job_id: UUID
    ) -> tuple[AccessContext, ExportManifest, ReadScope, ExportStore]:
        current = await self.queue.policy.revalidate(access, action=Action.CONFIGURE)
        if kg not in current.kg_ids:
            raise inaccessible_reference()
        ns = {"org_id": str(current.org_id), "kg_id": str(kg)}
        row = await self.database.jobs.find_one(
            ns | {"id": str(job_id), "kind": "export", "stage": "ready"}
        )
        if row is None:
            raise DomainError(ErrorCode.REVISION_NOT_READY, "No completed export is available.")
        operation = await self.operations.authorize(current, row)
        raw = await self.database.export_results.find_one(
            ns | {"id": str(operation.id), "state": "sealed"}
        )
        try:
            if raw is None:
                raise ValueError("missing export")
            manifest = ExportManifest.model_validate(
                {key: raw[key] for key in ExportManifest.model_fields}
            )
            if (
                manifest.org_id,
                manifest.kg_id,
                manifest.export_id,
                manifest.job_id,
                str(manifest.attempt_id),
                manifest.snapshot,
                digest(manifest.model_dump(mode="json")),
            ) != (
                current.org_id,
                kg,
                operation.id,
                job_id,
                row["attempt_id"],
                operation.snapshot,
                row.get("accepted_operation_hash"),
            ) or row.get("accepted_operation_id") != str(operation.id):
                raise ValueError("export ownership differs")
            if not isinstance(operation.command, ExportCommand) or (
                manifest.format,
                manifest.filters_hash,
            ) != (
                operation.command.format,
                digest(operation.command.filters.model_dump(mode="json")),
            ):
                raise ValueError("export filters differ")
        except (ValidationError, ValueError, KeyError, TypeError):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "The export seal is inconsistent."
            ) from None
        if manifest.expires_at <= now():
            raise DomainError(
                ErrorCode.RESOURCE_NOT_ACCESSIBLE, "The export retention has expired."
            )
        if current.policy_revision != manifest.policy_revision:
            raise DomainError(ErrorCode.POLICY_CHANGED, "The export access scope changed.")
        await self._boundary(operation.snapshot)
        scope = await self._scope(current, operation)
        await self.graph.resolver.revalidate(scope)
        store = ExportStore((await self.queue.router.for_sources(current, kg)).blobs)
        return current, manifest, scope, store

    async def manifest(self, access: AccessContext, kg: UUID, job_id: UUID) -> ExportManifest:
        _, manifest, _, _ = await self._manifest(access, kg, job_id)
        return manifest

    async def download(
        self, access: AccessContext, kg: UUID, job_id: UUID
    ) -> AsyncGenerator[bytes, None]:
        current, manifest, scope, store = await self._manifest(access, kg, job_id)
        async for chunk in store.open(manifest.content.retained):
            await self.queue.policy.revalidate(current, action=Action.CONFIGURE)
            await self._boundary(manifest.snapshot)
            yield chunk
        await self.graph.resolver.revalidate(scope)

    async def accepted(self, job: LeasedJob) -> bool:
        row: dict[str, Any] | None = await self.database.jobs.find_one(
            self.queue._scope(job)
            | {
                "kind": "export",
                "stage": "ready",
                "attempt_id": str(job.attempt_id),
                "lease_token": str(job.lease_token),
                "fence": job.fence,
            }
        )
        if row is None:
            return False
        access = await self.queue.policy.resolve_access(
            Principal.model_validate(row["actor"]), [job.kg_id], Action.CONFIGURE, org_id=job.org_id
        )
        manifest = await self.manifest(access, job.kg_id, job.id)
        return manifest.export_id == job.target_id and manifest.attempt_id == job.attempt_id
