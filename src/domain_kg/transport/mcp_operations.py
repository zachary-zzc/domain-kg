"""Typed owner operations and import/export inspection for agents."""

from datetime import date
from typing import Annotated
from uuid import UUID

from mcp.types import CallToolResult
from pydantic import Field

from domain_kg.identity.models import Action
from domain_kg.imports.models import ImportPlan, ImportSource
from domain_kg.jobs.models import JobReceipt
from domain_kg.models import PageRequest
from domain_kg.operations.audit import AuditPage, AuditTrail
from domain_kg.operations.backup import BackupService
from domain_kg.operations.backup_models import BackupManifest
from domain_kg.operations.downloads import BackupDownloads
from domain_kg.operations.export import ExportService
from domain_kg.operations.export_models import ExportManifest
from domain_kg.operations.jobs import operation_action
from domain_kg.operations.metrics import ProviderUsage
from domain_kg.operations.models import OwnerCommand, RestoreCommand
from domain_kg.operations.packages import PackagePage
from domain_kg.operations.projections import ProjectionStatus, RepairCommand
from domain_kg.operations.restore_management import RestoreInfo
from domain_kg.operations.usage_history import UsageHistory, UsageHistoryService
from domain_kg.operations.work_metrics import ProjectionWork, ProjectionWorkMetrics
from domain_kg.transport.mcp_common import MCPTools, output
from domain_kg.transport.mcp_management import Offset, ReadLimit, file_chunk
from domain_kg.transport.mcp_models import FileChunk

DEFAULT_PAGE = PageRequest()


def register_operations(context: MCPTools) -> None:
    tool, access, services = context.tool, context.access, context.services

    @tool("plan_import")
    async def plan(
        command: ImportSource, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, ImportPlan]:
        """Validate a completed CSV/JSON/GraphML/LightRAG graph upload before importing."""
        return output(
            await services().imports.plan(
                await access((command.kg_id,), Action.INGEST, org_id), command
            ),
            "Graph import plan.",
        )

    @tool("execute_import", external=True)
    async def execute(
        plan: ImportPlan, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, JobReceipt]:
        """Execute the exact validated import plan; missing source evidence stays unverified."""
        return output(
            await services().imports.execute(
                await access((plan.kg_id,), Action.INGEST, org_id), plan
            ),
            "Graph import queued.",
        )

    @tool("submit_operation", destructive=True, external=True)
    async def submit(
        command: OwnerCommand, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, JobReceipt]:
        """Queue owner backup, export, restore, generation/reindex, curation repair or cleanup.

        kind selects the typed command. Bind expected_published_revision from get_kg.
        restore creates a NEW KG; restore_kg instead reverses recoverable deletion.
        Retention/restore cleanup only delete server-selected eligible retained objects.
        """
        current = await access((command.kg_id,), operation_action(command.kind), org_id)
        result = (
            await services().restores.submit(current, command)
            if isinstance(command, RestoreCommand)
            else await services().owner_operations.submit(current, command)
        )
        return output(result, "Owner operation queued; poll get_job.")

    @tool("get_backup", read=True)
    async def backup(
        kg_id: UUID, job_id: UUID, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, BackupManifest]:
        """Inspect a completed retained backup before download or restore."""
        return output(
            await BackupService(services().queue).manifest(
                await access((kg_id,), Action.BACKUP, org_id), kg_id, job_id
            ),
            "Backup manifest.",
        )

    @tool("get_backup_page", read=True)
    async def backup_page(
        kg_id: UUID, backup_id: UUID, index: Annotated[int, Field(ge=0)], org_id: UUID | None = None
    ) -> Annotated[CallToolResult, PackagePage]:
        """Read a bounded retained backup page and its typed owned object references."""
        return output(
            await BackupService(services().queue).parts(
                await access((kg_id,), Action.BACKUP, org_id), kg_id, backup_id, index
            ),
            "Backup page.",
        )

    @tool("read_backup_file", read=True)
    async def backup_file(
        kg_id: UUID,
        backup_id: UUID,
        index: Annotated[int, Field(ge=0)],
        object_id: UUID,
        offset: Offset = 0,
        limit: ReadLimit = 512 * 1024,
        org_id: UUID | None = None,
    ) -> Annotated[CallToolResult, FileChunk]:
        """Read bounded base64 bytes of an object owned by the selected retained backup page."""
        current = await access((kg_id,), Action.BACKUP, org_id)
        return output(
            await file_chunk(
                BackupDownloads(BackupService(services().queue)).stream(
                    current, kg_id, backup_id, index, object_id
                ),
                offset,
                limit,
            ),
            "Retained backup bytes.",
        )

    @tool("get_export", read=True)
    async def export(
        kg_id: UUID, job_id: UUID, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, ExportManifest]:
        """Inspect an authorized CSV/JSONL/GraphML export and its retention boundary."""
        return output(
            await ExportService(services().queue).manifest(
                await access((kg_id,), Action.CONFIGURE, org_id), kg_id, job_id
            ),
            "Export manifest.",
        )

    @tool("read_export_file", read=True)
    async def export_file(
        kg_id: UUID,
        job_id: UUID,
        offset: Offset = 0,
        limit: ReadLimit = 512 * 1024,
        org_id: UUID | None = None,
    ) -> Annotated[CallToolResult, FileChunk]:
        """Read bounded base64 bytes of a completed export; follow next_offset until eof."""
        current = await access((kg_id,), Action.CONFIGURE, org_id)
        return output(
            await file_chunk(
                ExportService(services().queue).download(current, kg_id, job_id), offset, limit
            ),
            "Authorized export bytes.",
        )

    @tool("get_restore", read=True)
    async def restore(
        kg_id: UUID, job_id: UUID, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, RestoreInfo]:
        """Inspect backup restoration, new KG approval, pending writes and cleanup availability."""
        return output(
            await services().restores.get(
                await access((kg_id,), Action.RESTORE, org_id), kg_id, job_id
            ),
            "Restore status.",
        )

    @tool("get_projections", read=True)
    async def projections(
        kg_id: UUID, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, ProjectionStatus]:
        """Check graph/vector readiness against the accepted manifest; owner only."""
        return output(
            await services().projections.status(
                await access((kg_id,), Action.CONFIGURE, org_id), kg_id
            ),
            "Projection readiness.",
        )

    @tool("repair_material", destructive=True)
    async def repair(
        kg_id: UUID, command: RepairCommand, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, JobReceipt]:
        """Queue repair of accepted source projections; does not trust unaccepted staged records."""
        return output(
            await services().projections.repair(
                await access((kg_id,), Action.CONFIGURE, org_id),
                kg_id,
                command.material_id,
                idempotency_key=command.idempotency_key,
            ),
            "Projection repair queued.",
        )

    @tool("get_usage", read=True)
    async def usage(
        kg_id: UUID, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, ProviderUsage]:
        """Read this KG's daily provider counters/reservations; null limits mean no local cap."""
        return output(
            await services().usage.get(await access((kg_id,), Action.CONFIGURE, org_id), kg_id),
            "Current KG usage.",
        )

    @tool("get_usage_history", read=True)
    async def history(
        kg_id: UUID,
        days: Annotated[int, Field(ge=1, le=31)] = 7,
        end_date: date | None = None,
        org_id: UUID | None = None,
    ) -> Annotated[CallToolResult, UsageHistory]:
        """Read owner-only daily usage history for up to 31 days."""
        return output(
            await UsageHistoryService(services().policy).get(
                await access((kg_id,), Action.CONFIGURE, org_id),
                kg_id,
                days=days,
                end_date=end_date,
            ),
            "KG usage history.",
        )

    @tool("get_audit", read=True)
    async def audit(
        kg_id: UUID, page: PageRequest = DEFAULT_PAGE, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, AuditPage]:
        """Page redacted owner-operation audit events without source/command payloads."""
        return output(
            await AuditTrail(services().queue).list(
                await access((kg_id,), Action.CONFIGURE, org_id), kg_id, page
            ),
            "Owner-operation audit.",
        )

    @tool("get_operation_metrics", read=True)
    async def metrics(
        kg_id: UUID, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, ProjectionWork]:
        """Read bounded pending/failed projection job counts and oldest queue age."""
        return output(
            await ProjectionWorkMetrics(services().policy).get(
                await access((kg_id,), Action.CONFIGURE, org_id), kg_id
            ),
            "Projection work metrics.",
        )
