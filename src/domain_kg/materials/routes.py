"""Thin streaming HTTP wrappers with verified callers and server-owned object keys."""

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import Field

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import AccessContext, Action
from domain_kg.identity.routes import IdentityRuntime
from domain_kg.materials.models import (
    AddMaterial,
    IngestReceipt,
    ReplaceMaterial,
    UploadCommand,
    UploadReceipt,
)
from domain_kg.materials.service import MaterialService
from domain_kg.models import SHA256, FrozenModel, IdempotencyKey


class UploadParameters(FrozenModel):
    media_type: Annotated[str, Field(min_length=1, max_length=150)]
    sha256: SHA256
    size_bytes: Annotated[int, Field(ge=1, le=50 * 1024 * 1024)]
    idempotency_key: IdempotencyKey
    target_material_id: UUID | None = None


def material_router(identity: IdentityRuntime, service: MaterialService) -> APIRouter:
    router = APIRouter(prefix="/api/v1/kgs/{kg_id}")

    async def access(
        request: Request, kg: UUID, action: Action, *, mutate: bool = False
    ) -> AccessContext:
        principal, member = await identity.actor(request, mutating=mutate)
        return await service.policy.resolve_access(principal, [kg], action, org_id=member.org_id)

    @router.post("/uploads")
    async def upload(
        request: Request, kg_id: UUID, query: Annotated[UploadParameters, Query()]
    ) -> UploadReceipt:
        action = Action.REPLACE if query.target_material_id else Action.INGEST
        context = await access(request, kg_id, action, mutate=True)
        if request.headers.get("content-type", "").split(";", 1)[0] != query.media_type:
            raise DomainError(
                ErrorCode.VALIDATION_ERROR, "Content-Type must match the upload type."
            )
        return await service.upload(
            context, UploadCommand(kg_id=kg_id, **query.model_dump()), request.stream()
        )

    @router.post("/materials")
    async def add(request: Request, kg_id: UUID, command: AddMaterial) -> IngestReceipt:
        if command.kg_id != kg_id:
            raise DomainError(ErrorCode.VALIDATION_ERROR, "KG identities must match.")
        return await service.add(await access(request, kg_id, Action.INGEST, mutate=True), command)

    @router.post("/materials/{material_id}/versions")
    async def replace(
        request: Request, kg_id: UUID, material_id: UUID, command: ReplaceMaterial
    ) -> IngestReceipt:
        if (command.kg_id, command.material_id) != (kg_id, material_id):
            raise DomainError(ErrorCode.VALIDATION_ERROR, "Material identities must match.")
        return await service.replace(
            await access(request, kg_id, Action.REPLACE, mutate=True), command
        )

    @router.get("/materials/{material_id}/versions/{version_id}/original")
    async def original(
        request: Request, kg_id: UUID, material_id: UUID, version_id: UUID
    ) -> StreamingResponse:
        context = await access(request, kg_id, Action.READ)
        stream = service.original(context, kg_id, material_id, version_id)
        # Authorize and verify the object before sending HTTP response headers.
        first = await anext(stream)

        async def body() -> AsyncIterator[bytes]:
            try:
                yield first
                async for part in stream:
                    yield part
            finally:
                await stream.aclose()

        return StreamingResponse(
            body(),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": 'attachment; filename="original"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            },
        )

    from domain_kg.jobs.queue import JobQueue
    from domain_kg.materials.image_sources import ImageSources
    from domain_kg.materials.inspection_models import ImageSourceView

    images = ImageSources(JobQueue(service.policy, service.router))

    @router.get("/materials/{material_id}/versions/{version_id}/images")
    async def image_list(
        request: Request, kg_id: UUID, material_id: UUID, version_id: UUID
    ) -> tuple[ImageSourceView, ...]:
        return await images.list(
            await access(request, kg_id, Action.READ), kg_id, material_id, version_id
        )

    async def image_response(
        request: Request,
        kg_id: UUID,
        material_id: UUID,
        version_id: UUID,
        image_id: UUID,
        *,
        preview: bool,
    ) -> StreamingResponse:
        stream = images.stream(
            await access(request, kg_id, Action.READ),
            kg_id,
            material_id,
            version_id,
            image_id,
            preview=preview,
        )
        first = await anext(stream)

        async def body() -> AsyncIterator[bytes]:
            try:
                yield first
                async for part in stream:
                    yield part
            finally:
                await stream.aclose()

        return StreamingResponse(
            body(),
            media_type="image/png" if preview else "application/octet-stream",
            headers={
                "Content-Disposition": 'inline; filename="preview.png"'
                if preview
                else 'attachment; filename="source-image"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            },
        )

    @router.get("/materials/{material_id}/versions/{version_id}/images/{image_id}")
    async def image_original(
        request: Request, kg_id: UUID, material_id: UUID, version_id: UUID, image_id: UUID
    ) -> StreamingResponse:
        return await image_response(
            request, kg_id, material_id, version_id, image_id, preview=False
        )

    @router.get("/materials/{material_id}/versions/{version_id}/images/{image_id}/preview")
    async def image_preview(
        request: Request, kg_id: UUID, material_id: UUID, version_id: UUID, image_id: UUID
    ) -> StreamingResponse:
        return await image_response(request, kg_id, material_id, version_id, image_id, preview=True)

    return router
