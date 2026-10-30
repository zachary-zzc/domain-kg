"""Model setup has the same owner plus operator authorization as advanced settings."""

from typing import Annotated, Literal
from uuid import UUID

from mcp.types import CallToolResult

from domain_kg.identity.models import Action
from domain_kg.providers.configuration import ModelConfiguration, ModelSetup, ProfileApproval
from domain_kg.providers.health import ConnectionCheck, ProviderChecks
from domain_kg.providers.inspection import ModelSettings, inspect_models
from domain_kg.transport.mcp_common import MCPTools, output
from domain_kg.transport.mcp_models import ModelNames, ModelSaveResult
from domain_kg.transport.models import ProviderSelectionReceipt


def register_settings(context: MCPTools) -> None:
    tool, access, services = context.tool, context.access, context.services

    @tool("get_model_settings", read=True)
    async def settings(
        kg_id: UUID, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, ModelSettings]:
        """Read approved model choices, selection revision, context/image capability and last test.

        Endpoints/key-presence flags require owner + operator, like Advanced settings.
        API keys are never returned. Selection alone makes no model calls.
        """
        return output(
            await inspect_models(
                services().providers, await access((kg_id,), Action.CONFIGURE, org_id), kg_id
            ),
            "KG model settings.",
        )

    @tool("configure_models", destructive=True, external=True, idempotent=False)
    async def configure(
        kg_id: UUID,
        command: ModelConfiguration,
        test_connection: bool = True,
        org_id: UUID | None = None,
    ) -> Annotated[CallToolResult, ModelSaveResult]:
        """Owner + operator: configure chat/local/embedding connections and context windows.

        Tests synthetic Hello JSON + one embedding by default (provider charges may apply).
        A failed test saves nothing. Keys stay in the private provider file; never returned.
        Reuse credential_profile_id/embedding_profile_id to keep saved keys without retrieving them.
        data_approval='approved' is explicit permission for this KG's configured data policy.
        """
        current = await access((kg_id,), Action.CONFIGURE, org_id)
        providers = services().providers
        report = (
            await ProviderChecks(providers).configuration(current, kg_id, command)
            if test_connection
            else None
        )
        revision = (
            await ModelSetup(providers).save(current, kg_id, command)
            if report is None or report.ready
            else None
        )
        return output(
            ModelSaveResult(revision=revision, ready=revision is not None, check=report),
            "Model configuration saved."
            if revision is not None
            else "Connection test failed; nothing saved. Inspect check for billing/API errors.",
        )

    @tool("approve_model", destructive=True)
    async def approve(
        kg_id: UUID, command: ProfileApproval, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, ProviderSelectionReceipt]:
        """Owner + operator: approve a saved same-organization model for this KG and select it."""
        current = await access((kg_id,), Action.CONFIGURE, org_id)
        revision = await ModelSetup(services().providers).save(current, kg_id, command)
        return output(
            ProviderSelectionReceipt(kg_id=kg_id, profile_id=command.profile_id, revision=revision),
            "Saved model approved and selected.",
        )

    @tool("check_models", external=True, idempotent=False)
    async def check(
        kg_id: UUID,
        command: ModelConfiguration | None = None,
        force: bool = False,
        org_id: UUID | None = None,
    ) -> Annotated[CallToolResult, ConnectionCheck]:
        """Owner + operator: synthetic Hello/embedding test of selected or proposed configuration.

        May incur two small provider calls; force bypasses the test cache. Billing/key/rate errors
        are separate safe diagnostics. This does not send source materials or save configuration.
        """
        current = await access((kg_id,), Action.CONFIGURE, org_id)
        checks = ProviderChecks(services().providers)
        result = (
            await checks.selected(current, kg_id, force=force)
            if command is None
            else await checks.configuration(current, kg_id, command, force=force)
        )
        return output(
            result,
            "Model connection test passed."
            if result.ready
            else "Model connection test failed; inspect component diagnostics.",
        )

    @tool("discover_models", external=True, idempotent=False)
    async def discover(
        kg_id: UUID,
        component: Literal["chat", "embedding"],
        command: ModelConfiguration,
        org_id: UUID | None = None,
    ) -> Annotated[CallToolResult, ModelNames]:
        """Owner + operator: query available model names from a proposed local/API connection."""
        current = await access((kg_id,), Action.CONFIGURE, org_id)
        result = await ProviderChecks(services().providers).models(
            current, kg_id, component=component, command=command
        )
        return output(ModelNames(models=tuple(result)), "Available model names.")

    @tool("set_default_model", destructive=True)
    async def default(
        kg_id: UUID, profile_id: UUID, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, ModelSettings]:
        """Owner + operator: set an approved model as the new-KG default selection hint."""
        current = await access((kg_id,), Action.CONFIGURE, org_id)
        await ModelSetup(services().providers).set_default(current, kg_id, profile_id)
        return output(
            await inspect_models(services().providers, current, kg_id),
            "Default model updated; other KGs retain their selections.",
        )
