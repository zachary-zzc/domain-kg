"""Explicit editor forms translate to typed material commands and real job receipts."""

from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from domain_kg.console.forms import decode_form
from domain_kg.console.rendering import BrowserSession, browser_session, page
from domain_kg.console.workspace import filters_for, read_context
from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import AccessContext, Action
from domain_kg.materials.models import (
    AddMaterial,
    MaterialMetadataChanges,
    ReplaceMaterial,
    UpdateMaterialMetadata,
)
from domain_kg.transport.runtime import ServiceRuntime

SOURCE_FIELDS = {
    "title",
    "inline_text",
    "upload_id",
    "tags",
    "author",
    "source",
    "language",
    "idempotency_key",
}
META_FIELDS = {"title", "tags", "author", "source", "language"}


def invalid_form() -> DomainError:
    return DomainError(
        ErrorCode.VALIDATION_ERROR, "The form fields or revision are invalid."
    )


def tags(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))


def source_fields(fields: dict[str, str]) -> dict[str, Any]:
    return {
        "title": fields.get("title", ""),
        "inline_text": fields.get("inline_text") or None,
        "upload_id": fields.get("upload_id") or None,
        "tags": tags(fields.get("tags", "")),
        "author": fields.get("author") or None,
        "source": fields.get("source") or None,
        "language": fields.get("language") or None,
        "idempotency_key": fields.get("idempotency_key", ""),
    }


async def command_context(
    runtime: ServiceRuntime, request: Request, kg: UUID, action: Action
) -> tuple[dict[str, str], BrowserSession, AccessContext]:
    fields = decode_form(await request.body(), request.headers.get("content-type", ""))
    session = await browser_session(runtime, request, csrf=fields.pop("csrf_token", ""))
    access = await runtime.access(session.principal, (kg,), action, session.member.org_id)
    return fields, session, access


def redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303, headers={"Cache-Control": "no-store"})


def material_command_router(runtime: ServiceRuntime) -> APIRouter:
    router = APIRouter()

    async def form(
        request: Request,
        kg: UUID,
        operation: Literal["add", "replace", "metadata"],
        material: UUID | None = None,
    ) -> HTMLResponse:
        session, access, scope = await read_context(runtime, request, kg, filters_for(True))
        access = await runtime.policy.revalidate(
            access, action=Action.INGEST if operation == "add" else Action.REPLACE
        )
        detail = (
            await runtime.inspection.get(access, kg, material, include_unverified=True)
            if material
            else None
        )
        return await page(
            runtime,
            session,
            access,
            "material_form.html",
            {
                "title": {
                    "add": "Add material",
                    "replace": "Replace current version",
                    "metadata": "Edit material metadata",
                }[operation],
                "kg": await runtime.catalog.get_kg(access, kg),
                "operation": operation,
                "detail": detail,
                "command_key": str(uuid4()),
            },
            scope=scope,
        )

    @router.get("/kgs/{kg_id}/materials/new")
    async def new(request: Request, kg_id: UUID) -> HTMLResponse:
        return await form(request, kg_id, "add")

    @router.get("/kgs/{kg_id}/materials/{material_id}/replace")
    async def replacement(request: Request, kg_id: UUID, material_id: UUID) -> HTMLResponse:
        return await form(request, kg_id, "replace", material_id)

    @router.get("/kgs/{kg_id}/materials/{material_id}/metadata")
    async def metadata_form(request: Request, kg_id: UUID, material_id: UUID) -> HTMLResponse:
        return await form(request, kg_id, "metadata", material_id)

    @router.post("/kgs/{kg_id}/materials")
    async def add(request: Request, kg_id: UUID) -> RedirectResponse:
        fields, _, access = await command_context(runtime, request, kg_id, Action.INGEST)
        if set(fields) - (SOURCE_FIELDS | {"acl_mode"}):
            raise invalid_form()
        try:
            command = AddMaterial.model_validate(
                source_fields(fields) | {"kg_id": kg_id, "acl_mode": fields.get("acl_mode", "kg")}
            )
        except (ValueError, ValidationError):
            raise invalid_form() from None
        receipt = await runtime.materials.add(access, command)
        return redirect(f"/admin/kgs/{kg_id}/jobs/{receipt.job_id}")

    @router.post("/kgs/{kg_id}/materials/{material_id}/replace")
    async def replace(request: Request, kg_id: UUID, material_id: UUID) -> RedirectResponse:
        fields, _, access = await command_context(runtime, request, kg_id, Action.REPLACE)
        if set(fields) - (SOURCE_FIELDS | {"expected_active_version"}):
            raise invalid_form()
        try:
            command = ReplaceMaterial.model_validate(
                source_fields(fields)
                | {
                    "kg_id": kg_id,
                    "material_id": material_id,
                    "expected_active_version": fields.get("expected_active_version", ""),
                }
            )
        except (ValueError, ValidationError):
            raise invalid_form() from None
        receipt = await runtime.materials.replace(access, command)
        return redirect(f"/admin/kgs/{kg_id}/jobs/{receipt.job_id}")

    @router.post("/kgs/{kg_id}/materials/{material_id}/metadata")
    async def metadata(request: Request, kg_id: UUID, material_id: UUID) -> RedirectResponse:
        fields, _, access = await command_context(runtime, request, kg_id, Action.REPLACE)
        if set(fields) - (META_FIELDS | {"expected_revision", "idempotency_key"}):
            raise invalid_form()
        changes: dict[str, Any] = {
            key: value for key, value in fields.items() if key in META_FIELDS
        }
        if "tags" in changes:
            changes["tags"] = tags(changes["tags"])
        for key in ("author", "source", "language"):
            if key in changes and changes[key] == "":
                changes[key] = None
        try:
            command = UpdateMaterialMetadata(
                kg_id=kg_id,
                material_id=material_id,
                changes=MaterialMetadataChanges.model_validate(changes),
                expected_revision=int(fields.get("expected_revision", "")),
                idempotency_key=fields.get("idempotency_key", ""),
            )
        except (ValueError, ValidationError):
            raise invalid_form() from None
        await runtime.lifecycle.update_metadata(access, command)
        return redirect(f"/admin/kgs/{kg_id}/materials/{material_id}?include_unverified=true")

    return router
