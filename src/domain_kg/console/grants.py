"""Owner role and material ACL forms keep policy CAS and subject validation in services."""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from domain_kg.console.material_commands import command_context, invalid_form, redirect
from domain_kg.console.rendering import browser_session, page
from domain_kg.console.team import identifiers
from domain_kg.identity.grant_models import MemberRoleChange
from domain_kg.identity.material_grants import MaterialACLChange
from domain_kg.identity.models import Action
from domain_kg.models import PageRequest
from domain_kg.transport.runtime import ServiceRuntime


def grant_router(runtime: ServiceRuntime) -> APIRouter:
    router = APIRouter()

    @router.get("/kgs/{kg_id}/grants")
    async def grants(
        request: Request,
        kg_id: UUID,
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> HTMLResponse:
        session = await browser_session(runtime, request)
        access = await runtime.access(
            session.principal, (kg_id,), Action.MANAGE_GRANTS, session.member.org_id
        )
        result = await runtime.grants.list(access, kg_id, PageRequest(limit=limit, cursor=cursor))
        subjects: list[dict[str, str]] = []
        if session.member.role == "admin":
            for kind in ("members", "services", "groups"):
                directory = await runtime.identity.repository.list_page(
                    session.member, kind, PageRequest(limit=100)
                )
                subjects.extend(
                    {
                        "id": item["id"],
                        "name": item.get("name", item.get("subject", "")),
                        "kind": "group" if kind == "groups" else "member",
                    }
                    for item in directory["items"]
                )
        return await page(
            runtime,
            session,
            access,
            "grants.html",
            {
                "title": "KG roles",
                "kg": await runtime.catalog.get_kg(access, kg_id),
                "grants": result,
                "limit": limit,
                "command_key": str(uuid4()),
                "subjects": subjects,
                "subject_names": {item["id"]: item["name"] for item in subjects},
            },
        )

    @router.post("/kgs/{kg_id}/grants")
    async def set_role(request: Request, kg_id: UUID) -> RedirectResponse:
        fields, _, access = await command_context(runtime, request, kg_id, Action.MANAGE_GRANTS)
        if set(fields) != {
            "subject_kind",
            "subject_id",
            "role",
            "expected_revision",
            "idempotency_key",
        }:
            raise invalid_form()
        try:
            command = MemberRoleChange.model_validate(
                fields
                | {
                    "kg_id": kg_id,
                    "expected_revision": int(fields["expected_revision"]),
                    "role": fields["role"] or None,
                }
            )
        except (ValueError, ValidationError):
            raise invalid_form() from None
        await runtime.grants.set(access, command)
        return redirect(f"/admin/kgs/{kg_id}/grants")

    @router.get("/kgs/{kg_id}/materials/{material_id}/acl")
    async def acl(request: Request, kg_id: UUID, material_id: UUID) -> HTMLResponse:
        session = await browser_session(runtime, request)
        access = await runtime.access(
            session.principal, (kg_id,), Action.MANAGE_GRANTS, session.member.org_id
        )
        result = await runtime.material_grants.get(access, kg_id, material_id)
        return await page(
            runtime,
            session,
            access,
            "material_acl.html",
            {
                "title": "Material access",
                "kg": await runtime.catalog.get_kg(access, kg_id),
                "acl": result,
                "command_key": str(uuid4()),
            },
        )

    @router.post("/kgs/{kg_id}/materials/{material_id}/acl")
    async def set_acl(request: Request, kg_id: UUID, material_id: UUID) -> RedirectResponse:
        fields, _, access = await command_context(runtime, request, kg_id, Action.MANAGE_GRANTS)
        if set(fields) != {
            "acl_mode",
            "member_ids",
            "group_ids",
            "expected_revision",
            "idempotency_key",
        }:
            raise invalid_form()
        try:
            command = MaterialACLChange.model_validate(
                fields
                | {
                    "kg_id": kg_id,
                    "material_id": material_id,
                    "expected_revision": int(fields["expected_revision"]),
                    "member_ids": identifiers(fields["member_ids"]),
                    "group_ids": identifiers(fields["group_ids"]),
                }
            )
        except (ValueError, ValidationError):
            raise invalid_form() from None
        await runtime.material_grants.set(access, command)
        return redirect(f"/admin/kgs/{kg_id}/materials/{material_id}/acl")

    return router
