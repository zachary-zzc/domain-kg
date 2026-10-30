"""Session-bound browser routes call the same catalog and policy services."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from domain_kg.console.backups import backup_router
from domain_kg.console.batch import batch_router
from domain_kg.console.browser_tools import browser_tools_router
from domain_kg.console.curation import curation_router
from domain_kg.console.exports import export_router
from domain_kg.console.forms import read_form
from domain_kg.console.grants import grant_router
from domain_kg.console.i18n import language_router
from domain_kg.console.identity_bridge import identity_bridge_router
from domain_kg.console.jobs import job_router
from domain_kg.console.material_commands import material_command_router
from domain_kg.console.material_management import management_router
from domain_kg.console.model_settings import model_settings_router
from domain_kg.console.node_actions import node_action_router
from domain_kg.console.operations import operations_router
from domain_kg.console.rendering import browser_session, html, page
from domain_kg.console.setup import setup_router
from domain_kg.console.team import team_router
from domain_kg.console.workspace import workspace_router
from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.models import PageRequest
from domain_kg.transport.runtime import ServiceRuntime


def console_router(runtime: ServiceRuntime) -> APIRouter:
    router = APIRouter(prefix="/admin")
    router.include_router(language_router())
    router.include_router(batch_router(runtime))
    router.include_router(setup_router(runtime))
    router.include_router(model_settings_router(runtime))
    router.include_router(browser_tools_router(runtime))
    router.include_router(backup_router(runtime))
    router.include_router(export_router(runtime))
    router.include_router(curation_router(runtime))
    router.include_router(node_action_router(runtime))
    router.include_router(operations_router(runtime))
    router.include_router(team_router(runtime))
    router.include_router(identity_bridge_router(runtime))
    router.include_router(grant_router(runtime))
    router.include_router(job_router(runtime))
    router.include_router(material_command_router(runtime))
    router.include_router(management_router(runtime))
    router.include_router(workspace_router(runtime))

    @router.get("")
    async def catalog(
        request: Request, cursor: str | None = None, lifecycle: Literal["deleted"] | None = None
    ) -> HTMLResponse:
        if not request.cookies.get("domain_kg_session"):
            return html("login.html", {"title": "Domain KG"})
        session = await browser_session(runtime, request)
        access = await runtime.access(session.principal, (), Action.LIST_KGS, session.member.org_id)
        result = await runtime.catalog.list_kgs(
            access, PageRequest(cursor=cursor), lifecycle=lifecycle
        )
        return await page(
            runtime,
            session,
            access,
            "catalog.html",
            {
                "title": "Deleted knowledge graphs" if lifecycle else "Knowledge graphs",
                "catalog": result,
                "lifecycle": lifecycle,
            },
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
        fields = await read_form(request)
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
