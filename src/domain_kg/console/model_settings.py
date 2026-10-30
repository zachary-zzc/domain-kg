"""Advanced browser model settings use current owner/operator service boundaries."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import ValidationError

from domain_kg.console.i18n import translate
from domain_kg.console.material_commands import command_context, invalid_form, redirect
from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import AccessContext, Action
from domain_kg.models import Namespace
from domain_kg.providers.configuration import ModelConfiguration, ModelSetup
from domain_kg.providers.health import ConnectionCheck, ProviderChecks
from domain_kg.providers.presets import PRESETS
from domain_kg.providers.profiles import ProviderProfile
from domain_kg.transport.runtime import ServiceRuntime

MODEL_COPY = (
    "Unlimited",
    "Testing chat and embedding connections…",
    "Saving tested configuration…",
    "Connection test passed.",
    "Connection test failed. Nothing was saved.",
    "Configuration saved.",
    "Reading available models…",
    "Model list loaded. Choose a model or type its name.",
    "No models were returned. Load a model locally or enter its exact name.",
    "The request could not be completed. Check your session and try again.",
    "Chat model",
    "Embedding model",
    "Saved key is available. Leave blank to keep it for this endpoint.",
    "Enter a key for this API. Local servers may leave it blank.",
    "Test again",
    "Not tested.",
    "Hello JSON test passed.",
    "Embedding test passed.",
    "Fix the chat connection first, then test again.",
    "The provider usage budget is exhausted.",
    "The API key was rejected. Check or replace the key, then test again.",
    "The provider reports a billing problem. Check credits and payment status in its console.",
    "The provider allowance is exhausted. Check its account credits, quota and spending limits.",
    "The provider is rate limiting requests. Wait before trying again.",
    "The model or API route was not found. Check the model name and base URL.",
    "The model service could not complete the request. Check its URL, model and availability.",
    "Cannot connect or read a valid response. Check the API URL, loaded model and local server.",
    "The model did not return the expected Hello JSON. Check structured output settings.",
    "The approved provider did not return a valid response.",
    "Cannot read the model list. Start the local server or enter a model name manually.",
    "The API did not return a supported model list.",
    "Enter a new API key when changing the API URL or protocol.",
    "Model selection changed. Refresh settings.",
    "Model configuration changed. Test again.",
    "Embedding changes require a new generation.",
    "The form fields are invalid.",
)


def check_payload(report: ConnectionCheck) -> dict[str, object]:
    return report.model_dump(mode="json") | {"ready": report.ready}


async def budget_payload(
    runtime: ServiceRuntime, access: AccessContext, kg: UUID
) -> dict[str, object]:
    usage = await runtime.usage.get(access, kg)
    return {
        "calls": usage.counts.spent_calls + usage.counts.reserved_calls,
        "call_limit": usage.limits.calls if usage.limits else None,
        "input": usage.counts.spent_input_tokens + usage.counts.reserved_input_tokens,
        "input_limit": usage.limits.input_tokens if usage.limits else None,
        "output": usage.counts.spent_output_tokens + usage.counts.reserved_output_tokens,
        "output_limit": usage.limits.output_tokens if usage.limits else None,
        "day": usage.day,
        "exhausted": usage.available is not None
        and (
            usage.available.calls == 0
            or usage.available.input_tokens == 0
            or usage.available.output_tokens == 0
        ),
    }


async def settings_context(
    runtime: ServiceRuntime,
    access: AccessContext,
    kg: UUID,
    selected: dict[str, object] | None,
    choices: list[ProviderProfile],
    *,
    operator: bool,
) -> dict[str, object]:
    registry = runtime.providers.registry
    registry.refresh()
    default = next((p for p in choices if p.id == registry.default_profile_id), None)
    current = next((p for p in choices if selected and str(p.id) == selected["profile_id"]), None)
    values: dict[str, object] | None = None
    report = None
    if current and operator:
        namespace = Namespace(org_id=access.org_id, kg_id=kg)
        profile, chat = registry.resolve(current.id, namespace, restricted=False)
        _, embedding = registry.resolve_embedding(current.id, namespace, restricted=False)
        source = "anthropic" if chat.api_format == "anthropic" else "custom"
        for preset in PRESETS:
            if preset.endpoint and preset.endpoint == chat.endpoint:
                source = preset.id
                break
        values = {
            "profile_id": str(profile.id),
            "name": profile.name or profile.extraction_model,
            "source": source,
            "endpoint": chat.endpoint,
            "api_format": chat.api_format,
            "has_key": bool(chat.api_key.get_secret_value()),
            "extraction_model": profile.extraction_model,
            "supports_images": profile.supports_images,
            "image_input_tokens": profile.image_input_tokens,
            "embedding_model": profile.embedding_model,
            "response_format": profile.extraction_response_format,
            "thinking": "default"
            if profile.extraction_enable_thinking is None
            else "enabled"
            if profile.extraction_enable_thinking
            else "disabled",
            "extraction_context_tokens": profile.extraction_context_tokens,
            "embedding_context_tokens": profile.embedding_context_tokens,
            "max_completion_tokens": profile.max_completion_tokens,
            "timeout_seconds": profile.timeout_seconds,
            "embedding_send_dimensions": profile.embedding_send_dimensions,
            "embedding_endpoint": embedding.endpoint,
            "allow_restricted": profile.allow_restricted,
        }
        try:
            report = await ProviderChecks(runtime.providers).last(access, kg)
        except DomainError as error:
            if error.code not in (ErrorCode.PROVIDER_UNAVAILABLE, ErrorCode.VERSION_CONFLICT):
                raise
    budget = await budget_payload(runtime, access, kg)
    await runtime.policy.revalidate(access)
    copy = {text: translate(text) for text in MODEL_COPY}
    copy.update({"error." + str(code): translate("error." + str(code)) for code in ErrorCode})
    return {
        "default_model": default,
        "current_model": values,
        "connection_check": report,
        "model_presets": [p.model_dump() | {"label": translate(p.label)} for p in PRESETS],
        "model_copy": copy,
        "model_budget": budget,
    }


def model_settings_router(runtime: ServiceRuntime) -> APIRouter:
    router = APIRouter()
    setup, checks = ModelSetup(runtime.providers), ProviderChecks(runtime.providers)

    async def form(
        request: Request, kg: UUID, *, test_only: bool = False, discovery: bool = False
    ) -> tuple[ModelConfiguration, AccessContext]:
        fields, _, access = await command_context(runtime, request, kg, Action.CONFIGURE)
        if test_only:
            # Synthetic Hello/model-list requests never submit or approve source materials.
            fields["data_approval"] = "approved"
        if discovery:
            for name in ("extraction_model", "embedding_model"):
                fields[name] = fields.get(name) or "model-discovery-only"
        for name in ("credential_profile_id", "embedding_profile_id"):
            if not fields.get(name):
                fields.pop(name, None)
        try:
            command = ModelConfiguration.model_validate(
                fields | {"expected_revision": int(fields.get("expected_revision", ""))}
            )
        except (ValueError, ValidationError):
            raise invalid_form() from None
        return command, access

    async def report_response(
        result: ConnectionCheck, access: AccessContext, kg: UUID
    ) -> dict[str, object]:
        return check_payload(result) | {"budget": await budget_payload(runtime, access, kg)}

    @router.post("/kgs/{kg_id}/settings/check")
    async def check(request: Request, kg_id: UUID) -> JSONResponse:
        fields, _, access = await command_context(runtime, request, kg_id, Action.CONFIGURE)
        if fields:
            raise invalid_form()
        result = await checks.selected(access, kg_id, force=True)
        return JSONResponse(
            await report_response(result, access, kg_id), headers={"Cache-Control": "no-store"}
        )

    @router.post("/kgs/{kg_id}/settings/models/test")
    async def test(request: Request, kg_id: UUID) -> JSONResponse:
        command, access = await form(request, kg_id, test_only=True)
        return JSONResponse(
            await report_response(
                await checks.configuration(access, kg_id, command, force=True), access, kg_id
            ),
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/kgs/{kg_id}/settings/models/test-and-save")
    async def checked_save(request: Request, kg_id: UUID) -> JSONResponse:
        command, access = await form(request, kg_id)
        result = await checks.configuration(access, kg_id, command)
        payload = await report_response(result, access, kg_id)
        if result.ready:
            await setup.save(access, kg_id, command)
            payload["next_url"] = f"/admin/kgs/{kg_id}/settings/advanced"
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.post("/kgs/{kg_id}/settings/models/{component}/discover")
    async def discover(
        request: Request, kg_id: UUID, component: Literal["chat", "embedding"]
    ) -> JSONResponse:
        command, access = await form(request, kg_id, test_only=True, discovery=True)
        result = await checks.models(access, kg_id, component=component, command=command)
        return JSONResponse({"models": result}, headers={"Cache-Control": "no-store"})

    @router.post("/kgs/{kg_id}/settings/default")
    async def default(request: Request, kg_id: UUID) -> RedirectResponse:
        fields, _, access = await command_context(runtime, request, kg_id, Action.CONFIGURE)
        if set(fields) != {"profile_id"}:
            raise invalid_form()
        try:
            identifier = UUID(fields["profile_id"])
        except ValueError:
            raise invalid_form() from None
        await setup.set_default(access, kg_id, identifier)
        return redirect(f"/admin/kgs/{kg_id}/settings/advanced")

    return router
