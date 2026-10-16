"""Session-bound browser routes call the same catalog and policy services."""

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from domain_kg.console.forms import decode_form
from domain_kg.console.rendering import browser_session, html, page
from domain_kg.console.workspace import workspace_router
from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.models import PageRequest
from domain_kg.transport.runtime import ServiceRuntime


def console_router(runtime: ServiceRuntime) -> APIRouter:
    router = APIRouter(prefix="/admin")
    router.include_router(workspace_router(runtime))

    @router.get("")
    async def catalog(request: Request, cursor: str | None = None) -> HTMLResponse:
        if not request.cookies.get("domain_kg_session"):
            return html("login.html", {"title": "Domain KG"})
        session = await browser_session(runtime, request)
        access = await runtime.access(session.principal, (), Action.LIST_KGS, session.member.org_id)
        result = await runtime.catalog.list_kgs(access, PageRequest(cursor=cursor))
        return await page(
            runtime,
            session,
            access,
            "catalog.html",
            {"title": "Knowledge graphs", "catalog": result},
        )

    @router.get("/kgs/{kg_id}")
    async def kg(request: Request, kg_id: UUID) -> HTMLResponse:
        session = await browser_session(runtime, request)
        access = await runtime.access(
            session.principal, (kg_id,), Action.READ, session.member.org_id
        )
        value = await runtime.catalog.get_kg(access, kg_id)
        return await page(
            runtime, session, access, "kg.html", {"title": value["name"], "kg": value}
        )

    @router.post("/logout")
    async def logout(request: Request) -> RedirectResponse:
        fields = decode_form(await request.body(), request.headers.get("content-type", ""))
        csrf = fields.pop("csrf_token", "")
        if fields:
            raise DomainError(ErrorCode.VALIDATION_ERROR, "The form fields are invalid.")
        session = await browser_session(runtime, request, csrf=csrf)
        await runtime.identity.sessions.revoke(session.session_id)
        response = RedirectResponse(
            "/admin", status_code=303, headers={"Cache-Control": "no-store"}
        )
        response.delete_cookie("domain_kg_session", path="/")
        response.delete_cookie("domain_kg_csrf", path="/")
        return response

    return router
