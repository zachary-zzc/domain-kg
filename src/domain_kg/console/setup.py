"""Browser setup calls the existing catalog and provider command boundaries."""

from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import Field, ValidationError

from domain_kg.catalog.models import CreateKG
from domain_kg.catalog.routes import KGRevisionCommand, KGStateChange
from domain_kg.console.forms import read_form
from domain_kg.console.material_commands import command_context, invalid_form, redirect
from domain_kg.console.model_settings import settings_context
from domain_kg.console.rendering import browser_session, page
from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.ingestion.models import ParseOptions
from domain_kg.models import Namespace, PageRequest
from domain_kg.providers.configuration import ModelConfiguration, ModelSetup, ProfileApproval
from domain_kg.providers.profiles import ProviderProfile, embedding_space_id
from domain_kg.transport.models import ProviderSelection
from domain_kg.transport.runtime import ServiceRuntime


class BrowserCreateKG(CreateKG):
    # Catalog persistence includes this browser-only intent in its idempotency comparison.
    model_profile_id: UUID | None = Field(default=None, exclude_if=lambda value: value is None)


def setup_router(runtime: ServiceRuntime) -> APIRouter:
    router = APIRouter()
    models = ModelSetup(runtime.providers)

    def reusable_profiles(org_id: UUID, operator: bool) -> list[ProviderProfile]:
        runtime.providers.registry.refresh()
        if not operator or runtime.providers.registry.config_path is None:
            return []
        return sorted(
            [
                profile
                for profile in runtime.providers.registry.profiles.values()
                if profile.data_policy_approved
                and profile.embedding_dimensions == runtime.catalog.router.dimensions
                and any(ns.org_id == org_id for ns in profile.allowed_namespaces)
            ],
            key=lambda profile: profile.id != runtime.providers.registry.default_profile_id,
        )

    @router.get("/kgs/new")
    async def new_kg(request: Request, cursor: str | None = None) -> HTMLResponse:
        session = await browser_session(runtime, request)
        access = await runtime.access(
            session.principal, (), Action.CREATE_KG, session.member.org_id
        )
        placements = await runtime.catalog.registry.inventory(
            access, "profiles", PageRequest(limit=100, cursor=cursor)
        )
        return await page(
            runtime,
            session,
            access,
            "kg_new.html",
            {
                "title": "Create knowledge graph",
                "placements": placements,
                "command_key": str(uuid4()),
                "model_choices": reusable_profiles(access.org_id, access.platform_operator),
                "default_profile_id": runtime.providers.registry.default_profile_id,
                "schemas": [
                    (runtime.catalog.general_profile_id, "General knowledge"),
                    (runtime.catalog.scientific_profile_id, "Scientific research"),
                    (runtime.catalog.medical_profile_id, "Medical knowledge"),
                ],
            },
        )

    @router.post("/kgs/new")
    async def create_kg(request: Request) -> RedirectResponse:
        fields = await read_form(request)
        session = await browser_session(runtime, request, csrf=fields.pop("csrf_token", ""))
        access = await runtime.access(
            session.principal, (), Action.CREATE_KG, session.member.org_id
        )
        try:
            if not fields.get("model_profile_id"):
                fields.pop("model_profile_id", None)
            if "name" in fields:
                fields["name"] = fields["name"].strip()
            command = BrowserCreateKG.model_validate(fields)
            profile_id = command.model_profile_id
            if profile_id and profile_id not in {
                profile.id for profile in reusable_profiles(access.org_id, access.platform_operator)
            }:
                raise DomainError(ErrorCode.FORBIDDEN, "Select an available model configuration.")
        except (ValueError, ValidationError):
            raise invalid_form() from None
        result = await runtime.catalog.create_kg(access, command)
        if profile_id:
            current = await runtime.access(
                session.principal, (result.kg_id,), Action.CONFIGURE, session.member.org_id
            )
            try:
                await models.save(
                    current,
                    result.kg_id,
                    ProfileApproval(
                        profile_id=profile_id,
                        data_approval="approved",
                        expected_revision=0,
                        idempotency_key=command.idempotency_key,
                    ),
                )
            except DomainError:
                # Creation already succeeded; keep the user in the created KG to finish setup.
                return redirect(f"/admin/kgs/{result.kg_id}/settings?setup_incomplete=true")
        return redirect(f"/admin/kgs/{result.kg_id}/settings")

    @router.get("/storage")
    async def storage(request: Request, cursor: str | None = None) -> HTMLResponse:
        session = await browser_session(runtime, request)
        access = await runtime.access(session.principal, (), Action.LIST_KGS, session.member.org_id)
        placements = await runtime.catalog.registry.inventory(
            access, "profiles", PageRequest(cursor=cursor)
        )
        return await page(
            runtime,
            session,
            access,
            "storage.html",
            {
                "title": "Storage profiles",
                "placements": placements,
            },
        )

    @router.get("/kgs/{kg_id}/settings")
    @router.get("/kgs/{kg_id}/settings/advanced")
    async def settings(
        request: Request, kg_id: UUID, setup_incomplete: bool = False, parser_saved: bool = False
    ) -> HTMLResponse:
        session = await browser_session(runtime, request)
        access = await runtime.access(
            session.principal, (kg_id,), Action.CONFIGURE, session.member.org_id
        )
        kg = await runtime.catalog.get_kg(access, kg_id)
        if kg["state"] == "deleted":
            return await page(
                runtime,
                session,
                access,
                "kg_deleted.html",
                {
                    "title": "Deleted knowledge graph",
                    "kg": kg,
                    "command_key": str(uuid4()),
                },
            )
        try:
            selected = await runtime.providers.selected(access, kg_id)
        except DomainError as error:
            if error.code != ErrorCode.PROVIDER_UNAVAILABLE:
                raise
            selected = None
        namespace = Namespace(org_id=access.org_id, kg_id=kg_id)
        choices = reusable_profiles(access.org_id, access.platform_operator)
        # Only display approved choices for this namespace; never serialize connections.
        profiles = [
            profile
            for profile in runtime.providers.registry.profiles.values()
            if profile.data_policy_approved
            and namespace in profile.allowed_namespaces
            and profile.embedding_dimensions == runtime.catalog.router.dimensions
        ]
        configured = False
        if selected:
            for profile in profiles:
                if str(profile.id) != selected["profile_id"]:
                    continue
                _, connection = runtime.providers.registry.resolve_embedding(
                    profile.id, namespace, restricted=False
                )
                configured = profile.revision == selected[
                    "profile_revision"
                ] and embedding_space_id(profile, connection) == selected.get("embedding_space_id")
        return await page(
            runtime,
            session,
            access,
            "settings.html",
            {
                "title": "KG settings",
                "kg": kg,
                "selected": selected,
                "profiles": profiles,
                "configured": configured,
                "command_key": str(uuid4()),
                "model_choices": choices,
                "can_configure_models": access.platform_operator
                and runtime.providers.registry.config_path is not None,
                "setup_incomplete": setup_incomplete,
                "dimensions": runtime.catalog.router.dimensions,
                "advanced_settings": request.url.path.endswith("/advanced"),
                "parser_configuration": await runtime.parser_setup.get(access, kg_id),
                "can_configure_parser": access.platform_operator
                and runtime.parser_setup.store.path is not None,
                "parser_saved": parser_saved,
            }
            | await settings_context(
                runtime, access, kg_id, selected, choices, operator=access.platform_operator
            ),
        )

    @router.post("/kgs/{kg_id}/settings/parser")
    async def parser_settings(request: Request, kg_id: UUID) -> RedirectResponse:
        fields, _, access = await command_context(runtime, request, kg_id, Action.CONFIGURE)
        numeric = {"memory_limit_mib", "timeout_seconds", "max_pages", "max_expanded_mib"}
        if set(fields) != numeric | {"expected_revision", "ocr"} or fields["ocr"] not in {
            "enabled",
            "disabled",
        }:
            raise invalid_form()
        try:
            values = {key: int(fields[key]) for key in numeric}
            expanded_mib = values.pop("max_expanded_mib")
            current = await runtime.parser_setup.get(access, kg_id)
            options = ParseOptions.model_validate(
                current.options.model_dump()
                | values
                | {
                    "max_expanded_bytes": expanded_mib * 1024 * 1024,
                    "ocr": fields["ocr"] == "enabled",
                }
            )
            revision = int(fields["expected_revision"])
            if revision < 0:
                raise ValueError("negative revision")
        except (ValueError, ValidationError):
            raise invalid_form() from None
        await runtime.parser_setup.save(access, kg_id, options, revision)
        return redirect(f"/admin/kgs/{kg_id}/settings/advanced?parser_saved=true#document-parsing")

    @router.post("/kgs/{kg_id}/lifecycle/{operation}")
    async def lifecycle(
        request: Request, kg_id: UUID, operation: Literal["update", "delete", "restore"]
    ) -> RedirectResponse:
        fields, _, access = await command_context(runtime, request, kg_id, Action.CONFIGURE)
        try:
            values = fields | {"expected_revision": int(fields.get("expected_revision", ""))}
            if operation == "update":
                command = KGStateChange.model_validate(values)
                await runtime.catalog.change_state(
                    access,
                    kg_id,
                    command.state,
                    name=command.name,
                    expected_revision=command.expected_revision,
                    idempotency_key=command.idempotency_key,
                )
            else:
                intent = KGRevisionCommand.model_validate(values)
                method = (
                    runtime.catalog.delete_kg
                    if operation == "delete"
                    else runtime.catalog.restore_kg
                )
                await method(
                    access,
                    kg_id,
                    expected_revision=intent.expected_revision,
                    idempotency_key=intent.idempotency_key,
                )
        except (ValueError, ValidationError):
            raise invalid_form() from None
        return redirect(
            "/admin?lifecycle=deleted" if operation == "delete" else f"/admin/kgs/{kg_id}/settings"
        )

    @router.post("/kgs/{kg_id}/settings/provider")
    async def provider(request: Request, kg_id: UUID) -> RedirectResponse:
        fields, _, access = await command_context(runtime, request, kg_id, Action.CONFIGURE)
        try:
            command = ProviderSelection.model_validate(
                fields | {"expected_revision": int(fields.get("expected_revision", ""))}
            )
        except (ValueError, ValidationError):
            raise invalid_form() from None
        await runtime.providers.bind(
            access,
            kg_id,
            command.profile_id,
            expected_revision=command.expected_revision,
            idempotency_key=command.idempotency_key,
        )
        return redirect(f"/admin/kgs/{kg_id}/settings")

    @router.post("/kgs/{kg_id}/settings/models")
    async def add_model(request: Request, kg_id: UUID) -> RedirectResponse:
        fields, _, access = await command_context(runtime, request, kg_id, Action.CONFIGURE)
        try:
            command = ModelConfiguration.model_validate(
                fields | {"expected_revision": int(fields.get("expected_revision", ""))}
            )
        except (ValueError, ValidationError):
            raise invalid_form() from None
        await models.save(access, kg_id, command)
        return redirect(f"/admin/kgs/{kg_id}/settings")

    @router.post("/kgs/{kg_id}/settings/approve-model")
    async def approve_model(request: Request, kg_id: UUID) -> RedirectResponse:
        fields, _, access = await command_context(runtime, request, kg_id, Action.CONFIGURE)
        try:
            command = ProfileApproval.model_validate(
                fields | {"expected_revision": int(fields.get("expected_revision", ""))}
            )
        except (ValueError, ValidationError):
            raise invalid_form() from None
        await models.save(access, kg_id, command)
        return redirect(f"/admin/kgs/{kg_id}/settings")

    return router
