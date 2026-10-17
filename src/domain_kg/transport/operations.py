"""Thin owner-operation routes; shared services own policy and acceptance."""

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.models import JobReceipt
from domain_kg.operations.backup import BackupService
from domain_kg.operations.backup_models import BackupManifest
from domain_kg.operations.downloads import BackupDownloads
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import BackupCommand
from domain_kg.operations.packages import PackagePage
from domain_kg.transport.runtime import ServiceRuntime


def operation_router(runtime: ServiceRuntime) -> APIRouter:
    router = APIRouter()

    def service() -> BackupService:
        return BackupService(runtime.queue)

    async def access(request: Request, kg: UUID, *, mutate: bool = False) -> AccessContext:
        principal, member = await runtime.identity.actor(request, mutating=mutate)
        return await runtime.access(principal, (kg,), Action.BACKUP, member.org_id)

    @router.post("/kgs/{kg_id}/backups", status_code=202)
    async def backup(request: Request, kg_id: UUID, command: BackupCommand) -> JobReceipt:
        if command.kg_id != kg_id:
            raise DomainError(ErrorCode.VALIDATION_ERROR, "Path and command identities must match.")
        return await OwnerOperations(runtime.queue).submit(
            await access(request, kg_id, mutate=True), command
        )

    @router.get("/kgs/{kg_id}/backups/jobs/{job_id}")
    async def manifest(request: Request, kg_id: UUID, job_id: UUID) -> BackupManifest:
        return await service().manifest(await access(request, kg_id), kg_id, job_id)

    @router.get("/kgs/{kg_id}/backups/{backup_id}/pages/{index}")
    async def parts(request: Request, kg_id: UUID, backup_id: UUID, index: int) -> PackagePage:
        return await service().parts(await access(request, kg_id), kg_id, backup_id, index)

    @router.get("/kgs/{kg_id}/backups/{backup_id}/pages/{index}/objects/{object_id}")
    async def download(
        request: Request, kg_id: UUID, backup_id: UUID, index: int, object_id: UUID
    ) -> StreamingResponse:
        stream = BackupDownloads(service()).stream(
            await access(request, kg_id), kg_id, backup_id, index, object_id
        )
        first = await anext(stream)  # Verify bytes before committing HTTP response headers.

        async def body() -> AsyncIterator[bytes]:
            try:
                yield first
                async for chunk in stream:
                    yield chunk
            finally:
                await stream.aclose()

        return StreamingResponse(
            body(),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="backup-{backup_id}-{object_id}.bin"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            },
        )

    return router
