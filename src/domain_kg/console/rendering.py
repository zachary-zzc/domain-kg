"""Escaped server templates, private responses and current browser session checks."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from domain_kg.console.i18n import LANGUAGE, RETURN_TO, label, language_url, translate
from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import AccessContext, Member, Principal
from domain_kg.revisions.models import ReadScope
from domain_kg.transport.runtime import ServiceRuntime

ROOT = Path(__file__).parent
TEMPLATES = Environment(
    loader=FileSystemLoader(ROOT / "templates"),
    autoescape=select_autoescape(default=True),
    undefined=StrictUndefined,
)
TEMPLATES.globals.update(_=translate, label=label, language_url=language_url)
TEMPLATES.filters["label"] = label
PRIVATE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'; object-src 'none'"
    ),
}


@dataclass(frozen=True)
class BrowserSession:
    principal: Principal
    member: Member
    session_id: str = field(repr=False)
    csrf: str = field(repr=False)


async def browser_session(
    runtime: ServiceRuntime, request: Request, *, csrf: str | None = None
) -> BrowserSession:
    identifier = request.cookies.get("domain_kg_session", "")
    if not identifier:
        raise DomainError(ErrorCode.UNAUTHENTICATED, "Sign in to continue.")
    if csrf is not None and (not csrf or request.headers.get("origin") != runtime.identity.origin):
        raise DomainError(ErrorCode.FORBIDDEN, "The form needs a current same-origin session.")
    principal = await runtime.identity.sessions.authenticate_session(identifier, csrf=csrf)
    org = await runtime.identity.sessions.organization(identifier)
    member = await runtime.identity.repository.require_member(principal, org)
    if member.kind != "human":
        raise DomainError(ErrorCode.FORBIDDEN, "Interactive access requires a human membership.")
    return BrowserSession(principal, member, identifier, request.cookies.get("domain_kg_csrf", ""))


def html(template: str, context: dict[str, Any], *, status: int = 200) -> HTMLResponse:
    if template not in {"kg.html", "node.html", "material.html"}:
        context = context | {"title": translate(context["title"])}
    return HTMLResponse(
        TEMPLATES.get_template(template).render(
            {
                "member": None,
                "csrf": "",
                "kg": None,
                "can_team": False,
                "can_operator": False,
                "locale": LANGUAGE.get(),
            }
            | context
        ),
        status_code=status,
        headers=PRIVATE_HEADERS | {"Content-Language": LANGUAGE.get()},
    )


async def page(
    runtime: ServiceRuntime,
    session: BrowserSession,
    access: AccessContext,
    template: str,
    context: dict[str, Any],
    *,
    scope: ReadScope | None = None,
) -> HTMLResponse:
    response = html(
        template,
        {
            "member": session.member,
            "csrf": session.csrf,
            "can_team": session.member.role == "admin",
            "can_operator": access.platform_operator,
            "can_edit": any(grant.role in ("owner", "editor") for grant in access.grants)
            and context.get("kg", {}).get("state") == "ready",
            "can_owner": any(grant.role == "owner" for grant in access.grants),
        }
        | context,
    )
    principal = await runtime.identity.sessions.authenticate_session(session.session_id)
    if principal != session.principal:
        raise DomainError(ErrorCode.POLICY_CHANGED, "The browser session changed.")
    await runtime.identity.repository.require_member(principal, session.member.org_id)
    await runtime.policy.revalidate(access)
    if scope is not None:
        await runtime.graph.resolver.revalidate(scope)
    return response


def error_page(error: DomainError, status: int) -> HTMLResponse:
    token = RETURN_TO.set("/admin")
    try:
        return html(
            "error.html", {"title": "Request could not be completed", "error": error}, status=status
        )
    finally:
        RETURN_TO.reset(token)
