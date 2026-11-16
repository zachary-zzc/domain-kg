"""Scoped browser inspection over the existing material, search and citation services."""

from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from domain_kg.console.browser_tools import folder_copy
from domain_kg.console.forms import read_form
from domain_kg.console.rendering import BrowserSession, browser_session, page
from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.inspection import JobFilters
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
        request: Request,
        kg_id: UUID,
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        include_unverified: bool = False,
        view: Literal["published", "processing", "attention", "removed"] = "published",
        add: Literal["files", "folder", "text"] | None = None,
        import_id: UUID | None = None,
        batch_id: UUID | None = None,
    ) -> HTMLResponse:
        filters = filters_for(include_unverified)
        scope = None
        if import_id is not None or view in ("processing", "attention"):
            session = await browser_session(runtime, request)
            access = await runtime.access(
                session.principal, (kg_id,), Action.READ, session.member.org_id
            )
        else:
            session, access, scope = await read_context(runtime, request, kg_id, filters)
        can_edit = any(grant.role in ("owner", "editor") for grant in access.grants)
        if (add or import_id or view == "removed") and not can_edit:
            await runtime.policy.revalidate(access, action=Action.INGEST)
        import_head = (
            await runtime.import_runs.head(access, kg_id, import_id) if import_id else None
        )
        # A selected run owns its rows; global history must not appear beneath it.
        materials = jobs = managed = None
        if import_head is None:
            if view == "published":
                materials = await runtime.inspection.list(
                    access, kg_id, PageRequest(limit=limit, cursor=cursor), filters
                )
            elif view == "removed":
                managed = await runtime.management.list(
                    access, kg_id, "removed", PageRequest(limit=limit, cursor=cursor)
                )
            else:
                jobs = await runtime.job_inspection.list(
                    access,
                    kg_id,
                    PageRequest(limit=limit, cursor=cursor),
                    JobFilters(
                        view="active" if view == "processing" else "attention", sources_only=True
                    ),
                )
        batch = await runtime.batch.get(access, kg_id, batch_id) if batch_id else None
        counts = {
            state: sum(item.outcome == state for item in batch.items) if batch else 0
            for state in ("submitted", "completed", "skipped", "failed", "pending")
        }
        bulk_kind = "jobs" if import_id or jobs else "removed" if managed else "materials"
        bulk_scope = (
            "#import-rows"
            if import_id
            else "#job-list"
            if jobs
            else "#managed-list"
            if managed
            else "#material-list"
        )
        response = await page(
            runtime,
            session,
            access,
            "materials.html",
            {
                "title": "Sources",
                "kg": await runtime.catalog.get_kg(access, kg_id),
                "materials": materials,
                "jobs": jobs,
                "managed": managed,
                "source_view": view,
                "add_mode": add,
                "import_id": import_id,
                "import_run": None,
                "import_head": import_head,
                "import_runs": await runtime.import_runs.list(access, kg_id) if can_edit else (),
                "folder_copy": folder_copy(),
                "batch": batch,
                "batch_counts": counts,
                "cursor": cursor,
                "limit": limit,
                "include_unverified": include_unverified,
                "command_key": str(uuid4()),
                "bulk_kind": bulk_kind,
                "bulk_scope": bulk_scope,
            },
            # Jobs and actor-owned import manifests are management metadata,
            # independent of an unrelated accepted graph publication.
            scope=scope,
        )
        if import_head is not None:
            await runtime.import_runs.head(access, kg_id, import_head.id)
        elif jobs is not None:
            for job in jobs.items:
                await runtime.queue.get(access, job.job_id)
        if (import_head is not None or jobs is not None) and batch is not None:
            fresh = await runtime.batch.get(access, kg_id, batch.batch_id)
            if tuple((item.id, item.title) for item in fresh.items) != tuple(
                (item.id, item.title) for item in batch.items
            ):
                raise DomainError(
                    ErrorCode.POLICY_CHANGED, "Batch source visibility changed during the request."
                )
        return response

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
            f"/admin/kgs/{kg_id}/materials?view=removed",
            status_code=303,
            headers={"Cache-Control": "no-store"},
        )

    return router
