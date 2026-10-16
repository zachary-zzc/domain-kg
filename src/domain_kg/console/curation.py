"""Thin source-qualified graph inspection, human edit and compensation forms."""

from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from domain_kg.console.material_commands import command_context, invalid_form, redirect, tags
from domain_kg.console.rendering import page
from domain_kg.console.team import identifiers
from domain_kg.console.workspace import filters_for, read_context
from domain_kg.identity.models import Action
from domain_kg.retrieval.models import SubgraphRequest
from domain_kg.revisions.inspection import CurationInspection
from domain_kg.revisions.models import HistoryRequest, NodeUpdate, RelationUpdate, RollbackRevision
from domain_kg.transport.runtime import ServiceRuntime

COMMON = {"curation_scope", "source_ids", "expected_revision", "reason", "idempotency_key"}
NODE_FIELDS = {"name", "entity_type", "description", "aliases", "tags"}
RELATION_FIELDS = {
    "subject_id",
    "target_id",
    "predicate",
    "polarity",
    "description",
    "confidence",
    "tags",
}


def curation_router(runtime: ServiceRuntime) -> APIRouter:
    router = APIRouter()

    @router.get("/kgs/{kg_id}/nodes/{entity_id}")
    async def node(
        request: Request,
        kg_id: UUID,
        entity_id: UUID,
        include_unverified: bool = False,
        material_id: UUID | None = None,
        version_id: UUID | None = None,
    ) -> HTMLResponse:
        filters = filters_for(include_unverified, material_id, version_id)
        session, access, scope = await read_context(runtime, request, kg_id, filters)
        value = await runtime.graph.get_node(access, kg_id, entity_id, filters)
        neighborhood = await runtime.graph.subgraph(
            access,
            SubgraphRequest(
                kg_id=kg_id,
                seed_ids=(entity_id,),
                max_hops=1,
                max_nodes=25,
                max_edges=40,
                filters=filters,
            ),
        )
        return await page(
            runtime,
            session,
            access,
            "node.html",
            {
                "title": value.name,
                "kg": await runtime.catalog.get_kg(access, kg_id),
                "node": value,
                "graph": neighborhood,
                "include_unverified": include_unverified,
                "material_id": material_id,
                "version_id": version_id,
            },
            scope=scope,
        )

    async def edit_form(
        request: Request,
        kg: UUID,
        target: UUID,
        kind: Literal["node", "relation"],
        public: bool,
        sources: str,
    ) -> HTMLResponse:
        try:
            selected = identifiers(sources)
        except ValueError:
            raise invalid_form() from None
        session, access, _ = await read_context(runtime, request, kg, filters_for(True))
        context = await CurationInspection(runtime.queue).edit(
            access, kg, target, kind, source_ids=selected, public=public
        )
        return await page(
            runtime,
            session,
            context.scope.access,
            "curation_form.html",
            {
                "title": f"Edit {kind}",
                "kg": await runtime.catalog.get_kg(access, kg),
                "edit": context,
                "kind": kind,
                "target_id": target,
                "command_key": str(uuid4()),
            },
            scope=context.scope,
        )

    @router.get("/kgs/{kg_id}/nodes/{target_id}/edit")
    async def node_form(
        request: Request, kg_id: UUID, target_id: UUID, public: bool = False, source_ids: str = ""
    ) -> HTMLResponse:
        return await edit_form(request, kg_id, target_id, "node", public, source_ids)

    @router.get("/kgs/{kg_id}/relations/{target_id}/edit")
    async def relation_form(
        request: Request, kg_id: UUID, target_id: UUID, public: bool = False, source_ids: str = ""
    ) -> HTMLResponse:
        return await edit_form(request, kg_id, target_id, "relation", public, source_ids)

    async def edit_post(
        request: Request, kg: UUID, target: UUID, kind: Literal["node", "relation"]
    ) -> RedirectResponse:
        fields, _, access = await command_context(runtime, request, kg, Action.CURATE)
        allowed = NODE_FIELDS if kind == "node" else RELATION_FIELDS
        if set(fields) - (COMMON | allowed) or not COMMON <= set(fields):
            raise invalid_form()
        try:
            changes: dict[str, Any] = {
                key: value for key, value in fields.items() if key in allowed
            }
            for key in ("tags", "aliases"):
                if key in changes:
                    changes[key] = tags(changes[key])
            if "confidence" in changes:
                changes["confidence"] = float(changes["confidence"])
            command_fields = {key: value for key, value in fields.items() if key in COMMON} | {
                "kg_id": kg,
                "source_ids": identifiers(fields["source_ids"]),
                "expected_revision": int(fields["expected_revision"]),
                "changes": changes,
            }
            result = (
                await runtime.revisions.update_node(
                    access, NodeUpdate.model_validate(command_fields | {"entity_id": target})
                )
                if kind == "node"
                else (
                    await runtime.revisions.update_relation(
                        access,
                        RelationUpdate.model_validate(command_fields | {"assertion_id": target}),
                    )
                )
            )
        except (ValueError, ValidationError):
            raise invalid_form() from None
        return redirect(f"/admin/kgs/{kg}/jobs/{result.job_id}")

    @router.post("/kgs/{kg_id}/nodes/{target_id}/edit")
    async def edit_node(request: Request, kg_id: UUID, target_id: UUID) -> RedirectResponse:
        return await edit_post(request, kg_id, target_id, "node")

    @router.post("/kgs/{kg_id}/relations/{target_id}/edit")
    async def edit_relation(request: Request, kg_id: UUID, target_id: UUID) -> RedirectResponse:
        return await edit_post(request, kg_id, target_id, "relation")

    async def history_page(
        request: Request,
        kg: UUID,
        target: UUID,
        kind: Literal["node", "relation"],
        cursor: str | None,
        include_suppressed: bool,
        include_unverified: bool,
    ) -> HTMLResponse:
        session, access, scope = await read_context(runtime, request, kg, filters_for(True))
        result = await runtime.revisions.get_revisions(
            access,
            HistoryRequest(
                kg_id=kg,
                target_id=target,
                kind=kind,
                cursor=cursor,
                include_suppressed=include_suppressed,
                include_unverified=include_unverified,
            ),
        )
        return await page(
            runtime,
            session,
            access,
            "history.html",
            {
                "title": "Revision history",
                "kg": await runtime.catalog.get_kg(access, kg),
                "history": result,
                "kind": kind,
                "target_id": target,
                "include_suppressed": include_suppressed,
                "include_unverified": include_unverified,
                "command_key": str(uuid4()),
            },
            scope=scope,
        )

    @router.get("/kgs/{kg_id}/nodes/{target_id}/history")
    async def node_history(
        request: Request,
        kg_id: UUID,
        target_id: UUID,
        cursor: str | None = None,
        include_suppressed: bool = False,
        include_unverified: bool = False,
    ) -> HTMLResponse:
        return await history_page(
            request, kg_id, target_id, "node", cursor, include_suppressed, include_unverified
        )

    @router.get("/kgs/{kg_id}/relations/{target_id}/history")
    async def relation_history(
        request: Request,
        kg_id: UUID,
        target_id: UUID,
        cursor: str | None = None,
        include_suppressed: bool = False,
        include_unverified: bool = False,
    ) -> HTMLResponse:
        return await history_page(
            request, kg_id, target_id, "relation", cursor, include_suppressed, include_unverified
        )

    @router.post("/kgs/{kg_id}/revisions/{revision_id}/rollback")
    async def rollback(request: Request, kg_id: UUID, revision_id: UUID) -> RedirectResponse:
        fields, _, access = await command_context(runtime, request, kg_id, Action.CURATE)
        if set(fields) != {"expected_current_revision", "reason", "idempotency_key"}:
            raise invalid_form()
        try:
            command = RollbackRevision.model_validate(
                fields
                | {
                    "kg_id": kg_id,
                    "revision_id": revision_id,
                    "expected_current_revision": int(fields["expected_current_revision"]),
                }
            )
        except (ValueError, ValidationError):
            raise invalid_form() from None
        result = await runtime.revisions.rollback(access, command)
        return redirect(
            f"/admin/kgs/{kg_id}/jobs/{result.job_id}" if result.job_id else f"/admin/kgs/{kg_id}"
        )

    return router
