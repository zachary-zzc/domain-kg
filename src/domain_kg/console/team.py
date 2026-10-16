"""Organization administrator forms over the shared external-identity repository."""

from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import StringConstraints, TypeAdapter, ValidationError

from domain_kg.console.forms import decode_form
from domain_kg.console.material_commands import invalid_form, redirect
from domain_kg.console.rendering import BrowserSession, browser_session, page
from domain_kg.identity.models import AccessContext, Action, Principal
from domain_kg.models import IdempotencyKey, NonNegative, PageRequest
from domain_kg.transport.runtime import ServiceRuntime

GroupName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


def identifiers(value: str) -> tuple[UUID, ...]:
    return tuple(UUID(part.strip()) for part in value.split(",") if part.strip())


def form_key(fields: dict[str, str]) -> str:
    return str(TypeAdapter(IdempotencyKey).validate_python(fields.get("idempotency_key", "")))


async def team_command(
    runtime: ServiceRuntime, request: Request
) -> tuple[dict[str, str], BrowserSession, AccessContext]:
    fields = decode_form(await request.body(), request.headers.get("content-type", ""))
    session = await browser_session(runtime, request, csrf=fields.pop("csrf_token", ""))
    access = await runtime.access(
        session.principal, (), Action.MANAGE_ORGANIZATION, session.member.org_id
    )
    return fields, session, access


def team_router(runtime: ServiceRuntime) -> APIRouter:
    router = APIRouter()

    @router.get("/team")
    async def team(
        request: Request,
        kind: Literal["members", "groups", "services"] = "members",
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> HTMLResponse:
        session = await browser_session(runtime, request)
        access = await runtime.access(
            session.principal, (), Action.MANAGE_ORGANIZATION, session.member.org_id
        )
        result = await runtime.identity.repository.list_page(
            session.member, kind, PageRequest(limit=limit, cursor=cursor)
        )
        return await page(
            runtime,
            session,
            access,
            "team.html",
            {
                "title": "Team administration",
                "kind": kind,
                "directory": result,
                "limit": limit,
                "command_key": str(uuid4()),
                "issuer": runtime.identity.settings.oidc_issuer,
            },
        )

    @router.post("/team/members")
    async def admit(request: Request) -> RedirectResponse:
        fields, session, _ = await team_command(runtime, request)
        if set(fields) - {"issuer", "subject", "role", "idempotency_key"}:
            raise invalid_form()
        try:
            target = Principal(issuer=fields.get("issuer", ""), subject=fields.get("subject", ""))
            role = TypeAdapter(Literal["admin", "member"]).validate_python(
                fields.get("role", "member")
            )
            key = form_key(fields)
        except (ValueError, ValidationError):
            raise invalid_form() from None
        await runtime.identity.repository.admit(
            session.member, target, role=role, idempotency_key=key
        )
        return redirect("/admin/team")

    @router.post("/team/services")
    async def admit_service(request: Request) -> RedirectResponse:
        fields, session, _ = await team_command(runtime, request)
        if set(fields) - {"issuer", "subject", "kg_ids", "scopes", "idempotency_key"}:
            raise invalid_form()
        try:
            target = Principal(
                issuer=fields.get("issuer", ""), subject=fields.get("subject", ""), kind="service"
            )
            kgs = identifiers(fields.get("kg_ids", ""))
            scopes = tuple(dict.fromkeys(fields.get("scopes", "").replace(",", " ").split()))
            key = form_key(fields)
        except (ValueError, ValidationError):
            raise invalid_form() from None
        await runtime.identity.repository.admit(
            session.member,
            target,
            role="member",
            kind="service",
            kg_ids=kgs,
            scopes=scopes,
            idempotency_key=key,
        )
        return redirect("/admin/team?kind=services")

    @router.post("/team/members/{member_id}")
    async def change(request: Request, member_id: UUID) -> RedirectResponse:
        fields, session, _ = await team_command(runtime, request)
        if set(fields) - {"role", "status", "expected_revision", "idempotency_key"}:
            raise invalid_form()
        try:
            role = (
                TypeAdapter(Literal["admin", "member"]).validate_python(fields["role"])
                if "role" in fields
                else None
            )
            status = (
                TypeAdapter(Literal["active", "suspended"]).validate_python(fields["status"])
                if "status" in fields
                else None
            )
            revision = TypeAdapter(NonNegative).validate_python(
                int(fields.get("expected_revision", ""))
            )
            key = form_key(fields)
        except (ValueError, ValidationError):
            raise invalid_form() from None
        await runtime.identity.repository.change_member(
            session.member,
            member_id,
            expected_revision=revision,
            idempotency_key=key,
            role=role,
            suspended=(status == "suspended") if status else None,
        )
        return redirect("/admin/team")

    @router.post("/team/groups")
    async def create_group(request: Request) -> RedirectResponse:
        fields, session, _ = await team_command(runtime, request)
        if set(fields) != {"name", "idempotency_key"}:
            raise invalid_form()
        try:
            key = form_key(fields)
            name = TypeAdapter(GroupName).validate_python(fields["name"])
        except ValidationError:
            raise invalid_form() from None
        await runtime.identity.repository.create_group(session.member, name, idempotency_key=key)
        return redirect("/admin/team?kind=groups")

    @router.post("/team/groups/{group_id}/members")
    async def group_member(request: Request, group_id: UUID) -> RedirectResponse:
        fields, session, _ = await team_command(runtime, request)
        if set(fields) != {"member_id", "operation", "idempotency_key"}:
            raise invalid_form()
        try:
            identifier = UUID(fields["member_id"])
            operation = TypeAdapter(Literal["add", "remove"]).validate_python(fields["operation"])
            key = form_key(fields)
        except (ValueError, ValidationError):
            raise invalid_form() from None
        await runtime.identity.repository.set_group_member(
            session.member, group_id, identifier, present=operation == "add", idempotency_key=key
        )
        return redirect("/admin/team?kind=groups")

    return router
