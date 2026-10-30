"""Typed MCP tools call shared services using only verified request credentials."""

from typing import Annotated
from uuid import UUID

from mcp.server import MCPServer
from mcp.types import CallToolResult

from domain_kg.catalog.models import CreateKG, KGReceipt
from domain_kg.catalog.routes import KGStateChange
from domain_kg.graph.views import NodeView, SubgraphView
from domain_kg.identity.grant_models import GrantPage, MemberRoleChange, PolicyReceipt
from domain_kg.identity.models import Action
from domain_kg.jobs.models import JobReceipt
from domain_kg.materials.inspection_models import MaterialDetail, MaterialPage
from domain_kg.materials.models import (
    AddMaterial,
    IngestReceipt,
    RemovalReceipt,
    RemoveMaterial,
    ReplaceMaterial,
    RestorationReceipt,
    RestoreMaterial,
)
from domain_kg.models import PageRequest
from domain_kg.retrieval.models import SearchFilters, SearchRequest, SearchResponse, SubgraphRequest
from domain_kg.revisions.models import (
    CurationCommand,
    HistoryPage,
    HistoryRequest,
    NodeUpdate,
    RelationUpdate,
    RevisionReceipt,
    RollbackRevision,
)
from domain_kg.transport.mcp_common import MCPTools, output
from domain_kg.transport.models import (
    CurationResult,
    Health,
    KGPage,
    KGSummary,
    ProviderSelection,
    ProviderSelectionReceipt,
)
from domain_kg.transport.runtime import ServiceRuntime

DEFAULT_PAGE = PageRequest()
DEFAULT_FILTERS = SearchFilters()


def register_tools(server: MCPServer[None], runtime: ServiceRuntime | None) -> None:
    context = MCPTools(server, runtime)
    tool, access, services = context.tool, context.access, context.services
    from domain_kg.transport.mcp_management import register_management
    from domain_kg.transport.mcp_operations import register_operations
    from domain_kg.transport.mcp_settings import register_settings
    from domain_kg.transport.mcp_team import register_team

    register_management(context)
    register_operations(context)
    register_settings(context)
    register_team(context)

    @tool("list_kgs", read=True)
    async def list_kgs(
        page: PageRequest = DEFAULT_PAGE,
        lifecycle: str | None = None,
        org_id: UUID | None = None,
    ) -> Annotated[CallToolResult, KGPage]:
        """List only KGs the authenticated organization member can currently access."""
        current = await access((), Action.LIST_KGS, org_id)
        result = KGPage.model_validate(
            await services().catalog.list_kgs(current, page, lifecycle=lifecycle)
        )
        return output(result, f"{len(result.items)} visible KGs.")

    @tool("get_kg", read=True)
    async def get_kg(
        kg_id: UUID, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, KGSummary]:
        """Inspect KG configuration, with management fields restricted to its owner."""
        current = await access((kg_id,), Action.READ, org_id)
        result = KGSummary.model_validate(await services().catalog.get_kg(current, kg_id))
        return output(result, f"{result.name}: {result.state}.")

    @tool("create_kg")
    async def create_kg(
        command: CreateKG, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, KGReceipt]:
        """Create a KG using an approved placement and an idempotency key."""
        current = await access((), Action.CREATE_KG, org_id)
        return output(await services().catalog.create_kg(current, command), "KG creation accepted.")

    @tool("list_materials", read=True)
    async def list_materials(
        kg_id: UUID,
        page: PageRequest = DEFAULT_PAGE,
        filters: SearchFilters = DEFAULT_FILTERS,
        org_id: UUID | None = None,
    ) -> Annotated[CallToolResult, MaterialPage]:
        """Page accepted visible materials using current metadata filters."""
        current = await access((kg_id,), Action.READ, org_id)
        result = await services().inspection.list(current, kg_id, page, filters)
        return output(result, f"{len(result.items)} visible materials.")

    @tool("get_material", read=True)
    async def get_material(
        kg_id: UUID,
        material_id: UUID,
        version_id: UUID | None = None,
        org_id: UUID | None = None,
        include_unverified: bool = False,
    ) -> Annotated[CallToolResult, MaterialDetail]:
        """Inspect visible material metadata and accepted version summaries."""
        current = await access((kg_id,), Action.READ, org_id)
        result = await services().inspection.get(
            current, kg_id, material_id, version_id, include_unverified=include_unverified
        )
        return output(result, result.material.title)

    @tool("add_material", external=True)
    async def add_material(
        command: AddMaterial, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, IngestReceipt]:
        """Queue a material from completed upload ID or inline text; no filesystem paths."""
        current = await access((command.kg_id,), Action.INGEST, org_id)
        return output(
            await services().materials.add(current, command), "Material ingestion queued."
        )

    @tool("update_material", external=True)
    async def update_material(
        command: ReplaceMaterial, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, IngestReceipt]:
        """Queue a replacement while the accepted source remains visible until publication."""
        current = await access((command.kg_id,), Action.REPLACE, org_id)
        return output(
            await services().materials.replace(current, command), "Material replacement queued."
        )

    @tool("remove_material", destructive=True)
    async def remove_material(
        command: RemoveMaterial, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, RemovalReceipt]:
        """Preview or apply immediate source exclusion or tombstoning."""
        current = await access((command.kg_id,), Action.REMOVE, org_id)
        return output(
            await services().lifecycle.remove(current, command), "Material removal processed."
        )

    @tool("restore_material", destructive=True)
    async def restore_material(
        command: RestoreMaterial, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, RestorationReceipt]:
        """Restore retained source visibility only after authorized readiness checks."""
        current = await access((command.kg_id,), Action.REPLACE, org_id)
        return output(
            await services().lifecycle.restore(current, command), "Material restoration accepted."
        )

    @tool("get_job", read=True)
    async def get_job(
        kg_id: UUID, job_id: UUID, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, JobReceipt]:
        """Inspect durable job progress under the current material or KG permissions."""
        current = await access((kg_id,), Action.READ, org_id)
        return output(await services().queue.get(current, job_id), "Current job status.")

    @tool("search", read=True, external=True)
    async def search(
        command: SearchRequest, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, SearchResponse]:
        """Search explicit KGs with filtered fast/deep recall and source-grounded evidence."""
        current = await access(command.kg_ids, Action.SEARCH, org_id)
        result = await services().search.search(current, command)
        return output(result, f"{len(result.results)} visible evidence results; {result.status}.")

    @tool("get_node", read=True)
    async def get_node(
        kg_id: UUID,
        entity_id: UUID,
        filters: SearchFilters = DEFAULT_FILTERS,
        org_id: UUID | None = None,
    ) -> Annotated[CallToolResult, NodeView]:
        """Inspect a visible node and independently scoped source/manual support."""
        current = await access((kg_id,), Action.READ, org_id)
        result = await services().graph.get_node(current, kg_id, entity_id, filters)
        return output(result, result.name)

    @tool("get_subgraph", read=True)
    async def get_subgraph(
        command: SubgraphRequest, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, SubgraphView]:
        """Explore a bounded graph with permissions and visibility at every step."""
        current = await access((command.kg_id,), Action.READ, org_id)
        result = await services().graph.subgraph(current, command)
        return output(result, f"{len(result.nodes)} visible nodes and {len(result.edges)} edges.")

    @tool("update_node", destructive=True)
    async def update_node(
        command: NodeUpdate, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, RevisionReceipt]:
        """Queue typed human node corrections with revision and source-scope checks."""
        current = await access((command.kg_id,), Action.CURATE, org_id)
        return output(
            await services().revisions.update_node(current, command), "Node revision queued."
        )

    @tool("update_relation", destructive=True)
    async def update_relation(
        command: RelationUpdate, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, RevisionReceipt]:
        """Queue human relationship changes while retaining independent source evidence."""
        current = await access((command.kg_id,), Action.CURATE, org_id)
        return output(
            await services().revisions.update_relation(current, command),
            "Relationship revision queued.",
        )

    @tool("curate_node", destructive=True)
    async def curate_node(
        command: CurationCommand, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, CurationResult]:
        """Preview or apply suppression, restoration, merge or split under current authority."""
        current = await access((command.kg_id,), Action.CURATE, org_id)
        result = CurationResult(result=await services().revisions.curate_node(current, command))
        return output(result, "Node curation processed.")

    @tool("get_revisions", read=True)
    async def get_revisions(
        command: HistoryRequest, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, HistoryPage]:
        """Page visible human before/after history with current permission and cursor checks."""
        current = await access((command.kg_id,), Action.READ, org_id)
        result = await services().revisions.get_revisions(current, command)
        return output(result, f"{len(result.items)} visible revisions.")

    @tool("rollback_revision", destructive=True)
    async def rollback_revision(
        command: RollbackRevision, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, CurationResult]:
        """Append an owner-authorized compensating revision without restoring old grants."""
        current = await access((command.kg_id,), Action.CURATE, org_id)
        result = CurationResult(result=await services().revisions.rollback(current, command))
        return output(result, "Compensating revision accepted.")

    @tool("update_kg", destructive=True)
    async def update_kg(
        kg_id: UUID, command: KGStateChange, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, KGReceipt]:
        """Change KG lifecycle state using an expected revision and idempotency key."""
        current = await access((kg_id,), Action.CONFIGURE, org_id)
        result = await services().catalog.change_state(
            current,
            kg_id,
            command.state,
            name=command.name,
            expected_revision=command.expected_revision,
            idempotency_key=command.idempotency_key,
        )
        return output(result, "KG state updated.")

    @tool("set_provider", destructive=True)
    async def set_provider(
        kg_id: UUID, command: ProviderSelection, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, ProviderSelectionReceipt]:
        """Select an operator-approved profile for this KG; requires current owner access."""
        current = await access((kg_id,), Action.CONFIGURE, org_id)
        revision = await services().providers.bind(
            current,
            kg_id,
            command.profile_id,
            expected_revision=command.expected_revision,
            idempotency_key=command.idempotency_key,
        )
        return output(
            ProviderSelectionReceipt(kg_id=kg_id, profile_id=command.profile_id, revision=revision),
            "Approved provider selected.",
        )

    @tool("list_members", read=True)
    async def list_members(
        kg_id: UUID, page: PageRequest = DEFAULT_PAGE, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, GrantPage]:
        """Page current KG grants; requires current owner access."""
        current = await access((kg_id,), Action.MANAGE_GRANTS, org_id)
        result = await services().grants.list(current, kg_id, page)
        return output(result, f"{len(result.items)} KG grants.")

    @tool("set_member_role", destructive=True)
    async def set_member_role(
        command: MemberRoleChange, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, PolicyReceipt]:
        """Set or revoke a member/group KG grant with expected policy revision."""
        current = await access((command.kg_id,), Action.MANAGE_GRANTS, org_id)
        return output(await services().grants.set(current, command), "KG policy updated.")

    @tool("get_health", read=True)
    async def get_health(
        kg_id: UUID, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, Health]:
        """Read redacted health for an authorized KG."""
        current = await access((kg_id,), Action.READ, org_id)
        return output(
            Health.model_validate(await services().catalog.health(current, kg_id)),
            "KG health checked.",
        )
