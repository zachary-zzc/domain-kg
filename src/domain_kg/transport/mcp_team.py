"""Organization and infrastructure tools retain the console's administrative boundaries."""

from typing import Annotated, Literal
from uuid import UUID

from mcp.types import CallToolResult

from domain_kg.catalog.models import BackendCapabilities, RegisterBackend, StorageProfile
from domain_kg.catalog.routes import BackendRegistration, ProfileRegistration
from domain_kg.errors import inaccessible_reference
from domain_kg.identity.models import Action, Group, Member, Principal
from domain_kg.identity.routes import AdmitMember, AdmitService, ChangeMember, CreateGroup
from domain_kg.models import IdempotencyKey, PageRequest
from domain_kg.transport.mcp_common import MCPTools, output
from domain_kg.transport.mcp_models import ApprovedReferences, BackendPage, TeamPage

DEFAULT_PAGE = PageRequest()


def register_team(context: MCPTools) -> None:
    tool, access, services = context.tool, context.access, context.services

    async def actor(org: UUID | None) -> Member:
        current = await access((), Action.MANAGE_ORGANIZATION, org)
        return await services().identity.repository.require_member(
            current.principal, current.org_id
        )

    @tool("list_team", read=True)
    async def team(
        kind: Literal["members", "services", "groups"] = "members",
        page: PageRequest = DEFAULT_PAGE,
        org_id: UUID | None = None,
    ) -> Annotated[CallToolResult, TeamPage]:
        """Organization admin: discover human/service/group IDs for KG and material grants."""
        return output(
            TeamPage.model_validate(
                await services().identity.repository.list_page(await actor(org_id), kind, page)
            ),
            "Organization identities.",
        )

    @tool("admit_member", destructive=True)
    async def admit(
        command: AdmitMember, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, Member]:
        """Organization admin: admit an existing external OIDC identity; never creates passwords."""
        return output(
            await services().identity.repository.admit(
                await actor(org_id),
                Principal(issuer=command.issuer, subject=command.subject),
                role=command.role,
                idempotency_key=command.idempotency_key,
            ),
            "Organization member admitted.",
        )

    @tool("admit_service", destructive=True)
    async def admit_service(
        command: AdmitService, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, Member]:
        """Organization admin: admit a service identity with explicit KG IDs and scopes."""
        return output(
            await services().identity.repository.admit(
                await actor(org_id),
                Principal(issuer=command.issuer, subject=command.subject),
                role=command.role,
                kind="service",
                kg_ids=command.kg_ids,
                scopes=command.scopes,
                idempotency_key=command.idempotency_key,
            ),
            "Scoped service identity admitted.",
        )

    @tool("change_member", destructive=True)
    async def change(
        member_id: UUID, command: ChangeMember, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, Member]:
        """Organization admin: change role/suspension or revoke a service via suspended=true.

        Expected member revision and last-active-admin/owner invariants are enforced.
        """
        return output(
            await services().identity.repository.change_member(
                await actor(org_id),
                member_id,
                expected_revision=command.expected_revision,
                role=command.role,
                suspended=command.suspended,
                idempotency_key=command.idempotency_key,
            ),
            "Organization identity updated.",
        )

    @tool("create_group", destructive=True)
    async def group(
        command: CreateGroup, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, Group]:
        """Organization admin: create a local group for KG grants."""
        return output(
            await services().identity.repository.create_group(
                await actor(org_id), command.name, idempotency_key=command.idempotency_key
            ),
            "Group created.",
        )

    @tool("set_group_member", destructive=True)
    async def group_member(
        group_id: UUID,
        member_id: UUID,
        present: bool,
        idempotency_key: IdempotencyKey,
        org_id: UUID | None = None,
    ) -> Annotated[CallToolResult, Group]:
        """Organization admin: add/remove one group member, preserving active owner invariants."""
        return output(
            await services().identity.repository.set_group_member(
                await actor(org_id),
                group_id,
                member_id,
                present=present,
                idempotency_key=idempotency_key,
            ),
            "Group membership updated.",
        )

    @tool("list_backends", read=True)
    async def backends(
        page: PageRequest = DEFAULT_PAGE, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, BackendPage]:
        """Platform operator: page registered backends without URLs, DSNs or credentials."""
        current = await access((), Action.REGISTER_BACKEND, org_id)
        return output(
            BackendPage.model_validate(
                await services().catalog.registry.inventory(current, "backends", page)
            ),
            "Registered backends.",
        )

    @tool("list_approved_backend_references", read=True)
    async def approved(
        page: PageRequest = DEFAULT_PAGE, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, ApprovedReferences]:
        """Platform operator: discover preconfigured connection IDs, without secret values."""
        current = await access((), Action.REGISTER_BACKEND, org_id)
        return output(
            ApprovedReferences.model_validate(
                await services().catalog.registry.inventory(current, "approved", page)
            ),
            "Approved backend references.",
        )

    @tool("register_backend", destructive=True, external=True)
    async def register(
        command: BackendRegistration, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, BackendCapabilities]:
        """Platform operator: register and probe a preconfigured connection ID."""
        current = await access((), Action.REGISTER_BACKEND, org_id)
        return output(
            await services().catalog.registry.register(
                current,
                RegisterBackend(kind=command.kind, secret_ref=command.secret_ref),
                idempotency_key=command.idempotency_key,
            ),
            "Approved backend registered.",
        )

    @tool("validate_backend", external=True)
    async def validate(
        backend_id: UUID, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, BackendCapabilities]:
        """Platform operator: run the same approved-backend capability check as the console."""
        current = await access((), Action.REGISTER_BACKEND, org_id)
        registry = services().catalog.registry
        records = await registry.list_backends(current)
        target = next((record for record in records if record.backend_id == backend_id), None)
        if target is None:
            raise inaccessible_reference()
        result = await registry.probe(
            backend_id, await registry.connection(backend_id, target.kind)
        )
        await services().policy.revalidate(current)
        return output(result, "Backend capability check completed.")

    @tool("register_storage_profile", destructive=True)
    async def storage(
        command: ProfileRegistration, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, StorageProfile]:
        """Platform operator: register a bundle of backend IDs and organization allowance."""
        current = await access((), Action.REGISTER_BACKEND, org_id)
        profile = StorageProfile.model_validate(command.model_dump(exclude={"idempotency_key"}))
        return output(
            await services().catalog.registry.register_profile(
                current, profile, idempotency_key=command.idempotency_key
            ),
            "Storage profile registered.",
        )
