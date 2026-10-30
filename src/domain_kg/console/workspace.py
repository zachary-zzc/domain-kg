"""Scoped browser inspection over the existing material, search and citation services."""

from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from domain_kg.console.forms import read_form
from domain_kg.console.rendering import BrowserSession, browser_session, page
from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import AccessContext, Action
from domain_kg.materials.models import RemoveMaterial
from domain_kg.models import PageRequest
from domain_kg.retrieval.models import SearchFilters, SearchRequest
from domain_kg.revisions.models import ReadScope
from domain_kg.transport.runtime import ServiceRuntime


def filters_for(
    include_unverified: bool, material: UUID | None = None, version: UUID | None = None
) -> SearchFilters:
    return SearchFilters(
        material_ids=(material,) if material else (),
        version_id=version,
        support_statuses=("verified", "model_interpreted", "manual", "imported_unverified")
        if include_unverified
        else ("verified", "model_interpreted", "manual"),
    )


async def read_context(
    runtime: ServiceRuntime,
    request: Request,
    kg: UUID,
    filters: SearchFilters,
) -> tuple[BrowserSession, AccessContext, ReadScope]:
    session = await browser_session(runtime, request)
    access = await runtime.access(session.principal, (kg,), Action.READ, session.member.org_id)
    scope = await runtime.graph.resolver.resolve(
        access,
        SearchRequest(kg_ids=(kg,), query="console read", filters=filters),
        kg,
    )
    return session, access, scope


def workspace_router(runtime: ServiceRuntime) -> APIRouter:
    router = APIRouter()

    @router.get("/kgs/{kg_id}/materials")
    async def materials(
        request: Request, kg_id: UUID, cursor: str | None = None, include_unverified: bool = False
    ) -> HTMLResponse:
        filters = filters_for(include_unverified)
        session, access, scope = await read_context(runtime, request, kg_id, filters)
        result = await runtime.inspection.list(access, kg_id, PageRequest(cursor=cursor), filters)
        return await page(
            runtime,
            session,
            access,
            "materials.html",
            {
                "title": "Sources",
                "kg": await runtime.catalog.get_kg(access, kg_id),
                "materials": result,
                "include_unverified": include_unverified,
                "command_key": str(uuid4()),
                "bulk_kind": "materials",
                "bulk_scope": "#material-list",
            },
            scope=scope,
        )

    @router.get("/kgs/{kg_id}/materials/{material_id}")
    async def material(
        request: Request,
        kg_id: UUID,
        material_id: UUID,
        version_id: UUID | None = None,
        include_unverified: bool = False,
    ) -> HTMLResponse:
        filters = filters_for(include_unverified, material_id, version_id)
        session, access, scope = await read_context(runtime, request, kg_id, filters)
        detail = await runtime.inspection.get(
            access, kg_id, material_id, version=version_id, include_unverified=include_unverified
        )
        return await page(
            runtime,
            session,
            access,
            "material.html",
            {
                "title": detail.material.title,
                "kg": await runtime.catalog.get_kg(access, kg_id),
                "detail": detail,
                "include_unverified": include_unverified,
                "command_key": str(uuid4()),
            },
            scope=scope,
        )

    @router.get("/kgs/{kg_id}/search")
    async def search(
        request: Request,
        kg_id: UUID,
        q: Annotated[str, Query(max_length=2000)] = "",
        mode: Literal["fast", "deep", "vector", "lexical", "graph"] = "fast",
        include_unverified: bool = False,
        material_id: UUID | None = None,
    ) -> HTMLResponse:
        filters = filters_for(include_unverified, material_id)
        session, access, scope = await read_context(runtime, request, kg_id, filters)
        response = (
            await runtime.search.search(
                access,
                SearchRequest(
                    kg_ids=(kg_id,),
                    query=q,
                    mode=mode,
                    filters=filters,
                ),
            )
            if q
            else None
        )
        return await page(
            runtime,
            session,
            access,
            "search.html",
            {
                "title": "RAG search",
                "kg": await runtime.catalog.get_kg(access, kg_id),
                "q": q,
                "mode": mode,
                "include_unverified": include_unverified,
                "response": response,
                "material_id": material_id,
            },
            scope=scope,
        )

    @router.get("/citations/{citation_id}")
    async def citation(
        request: Request,
        citation_id: UUID,
        kg_id: UUID,
        material_id: UUID | None = None,
        version_id: UUID | None = None,
        include_unverified: bool = False,
    ) -> HTMLResponse:
        if version_id is not None and material_id is None:
            raise DomainError(ErrorCode.VALIDATION_ERROR, "A version requires its material.")
        session, access, scope = await read_context(
            runtime, request, kg_id, filters_for(include_unverified, material_id, version_id)
        )
        result = await runtime.citations.resolve(
            access,
            kg_id,
            citation_id,
            material_id=material_id,
            version_id=version_id,
            include_unverified=include_unverified,
        )
        return await page(
            runtime,
            session,
            access,
            "citation.html",
            {
                "title": "Citation",
                "kg": await runtime.catalog.get_kg(access, kg_id),
                "citation": result,
            },
            scope=scope,
        )

    @router.post("/kgs/{kg_id}/materials/{material_id}/remove")
    async def remove(request: Request, kg_id: UUID, material_id: UUID) -> RedirectResponse:
        fields = await read_form(request)
        session = await browser_session(runtime, request, csrf=fields.pop("csrf_token", ""))
        access = await runtime.access(
            session.principal, (kg_id,), Action.REMOVE, session.member.org_id
        )
        if set(fields) - {"action", "expected_revision", "idempotency_key"}:
            raise DomainError(ErrorCode.VALIDATION_ERROR, "The form fields are invalid.")
        try:
            command = RemoveMaterial.model_validate(
                fields
                | {
                    "kg_id": kg_id,
                    "material_id": material_id,
                    "expected_revision": int(fields.get("expected_revision", "")),
                }
            )
        except (ValueError, ValidationError):
            raise DomainError(ErrorCode.VALIDATION_ERROR, "The form fields are invalid.") from None
        await runtime.lifecycle.remove(access, command)
        return RedirectResponse(
            f"/admin/kgs/{kg_id}/materials", status_code=303, headers={"Cache-Control": "no-store"}
        )

    return router
