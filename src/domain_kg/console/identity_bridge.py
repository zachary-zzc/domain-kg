"""Human approval and connection revocation over the shared identity bridge services."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from domain_kg.console.forms import read_form
from domain_kg.console.material_commands import invalid_form, redirect
from domain_kg.console.rendering import browser_session, page
from domain_kg.console.team import identifiers, team_command
from domain_kg.identity.bridge.models import (
    CONTROL_ACTIONS,
    DefaultGrant,
    GroupMapping,
    IntegrationChange,
    IntegrationCreate,
    LinkApproval,
    RevisionCommand,
)
from domain_kg.identity.models import Action
from domain_kg.models import PageRequest
from domain_kg.transport.runtime import ServiceRuntime

BRIDGE_ACTIONS = tuple(
    action for action in Action if action not in CONTROL_ACTIONS | {Action.LIST_KGS}
)


def integration_form(fields: dict[str, str]) -> IntegrationCreate:
    """Unique field names keep browser forms unambiguous without client-side scripts."""
    fixed = {"name", "profile_id", "additional_kg_ids", "auto_approve", "idempotency_key"}
    kg_ids = list(identifiers(fields.get("additional_kg_ids", "")))
    grants: list[DefaultGrant] = []
    mappings: list[GroupMapping] = []
    actions: list[Action] = []
    for key, value in fields.items():
        if key.startswith("kg_"):
            kg = UUID(key.removeprefix("kg_"))
            if value not in {"exclude", "allowed", "reader", "editor"}:
                raise ValueError("invalid KG choice")
            if value != "exclude":
                kg_ids.append(kg)
            if value in {"reader", "editor"}:
                grants.append(DefaultGrant.model_validate({"kg_id": kg, "role": value}))
        elif key.startswith("action_"):
            action = Action(key.removeprefix("action_"))
            if action not in BRIDGE_ACTIONS or value != "on":
                raise ValueError("invalid action choice")
            actions.append(action)
        elif key.startswith("group_"):
            group = UUID(key.removeprefix("group_"))
            if value:
                mappings.append(GroupMapping(external_group=value, group_id=group))
        elif key not in fixed:
            raise ValueError("unknown form field")
    if fields.get("auto_approve", "off") not in {"on", "off"}:
        raise ValueError("invalid approval choice")
    return IntegrationCreate(
        profile_id=UUID(fields.get("profile_id", "")),
        name=fields.get("name", ""),
        allowed_kg_ids=tuple(kg_ids),
        allowed_actions=tuple(actions),
        default_grants=tuple(grants),
        group_mappings=tuple(mappings),
        auto_approve=fields.get("auto_approve") == "on",
        idempotency_key=fields.get("idempotency_key", ""),
    )


def revision_form(fields: dict[str, str]) -> RevisionCommand:
    if set(fields) != {"expected_revision", "idempotency_key"}:
        raise ValueError("unknown form field")
    return RevisionCommand(
        expected_revision=int(fields["expected_revision"]),
        idempotency_key=fields["idempotency_key"],
    )


def identity_bridge_router(runtime: ServiceRuntime) -> APIRouter:
    router = APIRouter()

    @router.get("/identity-integrations")
    async def integrations(
        request: Request,
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> HTMLResponse:
        session = await browser_session(runtime, request)
        access = await runtime.access(
            session.principal, (), Action.MANAGE_ORGANIZATION, session.member.org_id
        )
        bridge = runtime.identity.bridge
        directory = await bridge.management.list_page(
            session.principal,
            session.member.org_id,
            "integrations",
            PageRequest(limit=limit, cursor=cursor),
        )
        config = bridge.control.config
        profiles = tuple(
            {"id": profile.id, "name": profile.name}
            for profile in (config.profiles if config else ())
            if session.member.org_id in profile.allowed_org_ids
        )
        catalog_access = await runtime.access(
            session.principal, (), Action.LIST_KGS, session.member.org_id
        )
        kgs = await runtime.catalog.list_kgs(catalog_access, PageRequest(limit=25))
        groups = await runtime.identity.repository.list_page(
            session.member, "groups", PageRequest(limit=25)
        )
        return await page(
            runtime,
            session,
            access,
            "identity_integrations.html",
            {
                "title": "Identity integrations",
                "directory": directory,
                "profiles": profiles,
                "kgs": kgs,
                "groups": groups,
                "actions": BRIDGE_ACTIONS,
                "limit": limit,
                "command_key": str(uuid4()),
            },
        )

    @router.post("/identity-integrations")
    async def create(request: Request) -> RedirectResponse:
        fields, _, access = await team_command(runtime, request)
        try:
            command = integration_form(fields)
        except (ValueError, ValidationError):
            raise invalid_form() from None
        await runtime.identity.bridge.control.create(access, command)
        return redirect("/admin/identity-integrations")

    @router.post("/identity-integrations/{integration_id}")
    async def change(request: Request, integration_id: UUID) -> RedirectResponse:
        fields, _, access = await team_command(runtime, request)
        try:
            active = fields.pop("active")
            if active not in {"true", "false"}:
                raise ValueError("invalid status")
            revision = revision_form(fields)
            command = IntegrationChange(**revision.model_dump(), active=active == "true")
        except (KeyError, ValueError, ValidationError):
            raise invalid_form() from None
        await runtime.identity.bridge.control.change(access, integration_id, command)
        return redirect("/admin/identity-integrations")

    @router.get("/identity-integrations/{integration_id}/{kind}")
    async def integration_directory(
        request: Request,
        integration_id: UUID,
        kind: Literal["users", "connections"],
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> HTMLResponse:
        session = await browser_session(runtime, request)
        access = await runtime.access(
            session.principal, (), Action.MANAGE_ORGANIZATION, session.member.org_id
        )
        directory = await runtime.identity.bridge.management.list_page(
            session.principal,
            session.member.org_id,
            "links" if kind == "users" else "connections",
            PageRequest(limit=limit, cursor=cursor),
            integration_id=integration_id,
        )
        return await page(
            runtime,
            session,
            access,
            "identity_connections.html",
            {
                "title": "Integration users" if kind == "users" else "Integration connections",
                "directory": directory,
                "kind": kind,
                "own": False,
                "integration_id": integration_id,
                "limit": limit,
                "command_key": str(uuid4()),
                "now": datetime.now(UTC),
            },
        )

    @router.post("/identity-links/{link_id}/approval")
    async def approve(request: Request, link_id: UUID) -> RedirectResponse:
        fields, _, access = await team_command(runtime, request)
        try:
            approved = fields.pop("approved")
            if approved not in {"true", "false"}:
                raise ValueError("invalid approval")
            revision = revision_form(fields)
            command = LinkApproval(**revision.model_dump(), approved=approved == "true")
        except (KeyError, ValueError, ValidationError):
            raise invalid_form() from None
        await runtime.identity.bridge.management.approve(access, link_id, command)
        return redirect("/admin/identity-integrations")

    @router.get("/connections")
    async def own_connections(
        request: Request,
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> HTMLResponse:
        session = await browser_session(runtime, request)
        access = await runtime.access(session.principal, (), Action.LIST_KGS, session.member.org_id)
        directory = await runtime.identity.bridge.management.list_page(
            session.principal,
            session.member.org_id,
            "connections",
            PageRequest(limit=limit, cursor=cursor),
            own=True,
        )
        return await page(
            runtime,
            session,
            access,
            "identity_connections.html",
            {
                "title": "Agent connections",
                "directory": directory,
                "kind": "connections",
                "own": True,
                "integration_id": None,
                "limit": limit,
                "command_key": str(uuid4()),
                "now": datetime.now(UTC),
            },
        )

    @router.post("/connections/{connection_id}/revoke")
    async def revoke(request: Request, connection_id: UUID) -> RedirectResponse:
        fields = await read_form(request)
        session = await browser_session(runtime, request, csrf=fields.pop("csrf_token", ""))
        try:
            command = revision_form(fields)
        except (ValueError, ValidationError):
            raise invalid_form() from None
        await runtime.identity.bridge.management.revoke(
            session.principal, session.member.org_id, connection_id, command, own=True
        )
        return redirect("/admin/connections")

    @router.post("/identity-connections/{connection_id}/revoke")
    async def admin_revoke(request: Request, connection_id: UUID) -> RedirectResponse:
        fields, session, _ = await team_command(runtime, request)
        try:
            command = revision_form(fields)
        except (ValueError, ValidationError):
            raise invalid_form() from None
        await runtime.identity.bridge.management.revoke(
            session.principal, session.member.org_id, connection_id, command, own=False
        )
        return redirect("/admin/identity-integrations")

    return router
