"""Agent catalog discovery, sources, jobs and graph editing reuse shared services."""

import base64
import binascii
from collections.abc import AsyncIterable
from typing import Annotated, Literal
from uuid import UUID

from mcp.types import CallToolResult
from pydantic import Field

from domain_kg.catalog.models import KGReceipt
from domain_kg.catalog.routes import KGRevisionCommand
from domain_kg.citations.models import Citation
from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.models import DomainSchema
from domain_kg.graph.schema import schema_for_kg
from domain_kg.identity.grant_models import PolicyReceipt
from domain_kg.identity.material_grants import MaterialACLChange, MaterialACLView
from domain_kg.identity.models import Action
from domain_kg.jobs.inspection import JobPage
from domain_kg.jobs.models import JobReceipt
from domain_kg.materials.folders import FolderCommand, FolderIngestion, FolderResult
from domain_kg.materials.image_sources import ImageSources
from domain_kg.materials.management import ManagedMaterialPage, ManagementState
from domain_kg.materials.models import (
    MaterialMetadataReceipt,
    UpdateMaterialMetadata,
    UploadCommand,
    UploadReceipt,
)
from domain_kg.materials.purge_models import PurgeMaterial, PurgeReceipt
from domain_kg.models import IdempotencyKey, PageRequest
from domain_kg.operations.batch import BatchCommand, BatchResult
from domain_kg.retrieval.models import SearchFilters
from domain_kg.revisions.inspection import CurationInspection
from domain_kg.transport.mcp_common import MCPTools, output
from domain_kg.transport.mcp_models import (
    DomainChoice,
    DomainChoices,
    EditView,
    FileChunk,
    GraphOverview,
    ImagePage,
    StorageChoices,
)

DEFAULT_PAGE = PageRequest()
DEFAULT_FILTERS = SearchFilters()
Offset = Annotated[int, Field(ge=0, le=50 * 1024 * 1024)]
ReadLimit = Annotated[int, Field(ge=1, le=512 * 1024)]


async def file_chunk(stream: AsyncIterable[bytes], offset: int, limit: int) -> FileChunk:
    parts = []
    position = 0
    # Complete the bounded stream so its final visibility/integrity check runs too.
    async for part in stream:
        start, end = max(offset - position, 0), min(offset + limit - position, len(part))
        if end > start:
            parts.append(part[start:end])
        position += len(part)
    if offset > position:
        raise DomainError(ErrorCode.VALIDATION_ERROR, "File offset exceeds its length.")
    content = b"".join(parts)
    return FileChunk(
        content_base64=base64.b64encode(content).decode("ascii"),
        offset=offset,
        next_offset=offset + len(content),
        total_bytes=position,
        eof=offset + len(content) == position,
    )


def register_management(context: MCPTools) -> None:
    tool, access, services = context.tool, context.access, context.services

    @tool("list_storage_profiles", read=True)
    async def placements(
        page: PageRequest = DEFAULT_PAGE, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, StorageChoices]:
        """Discover approved placement IDs before creating or restoring a KG."""
        current = await access((), Action.LIST_KGS, org_id)
        result = await services().catalog.registry.inventory(current, "profiles", page)
        return output(StorageChoices.model_validate(result), "Approved storage placements.")

    @tool("list_domain_profiles", read=True)
    async def domains(org_id: UUID | None = None) -> Annotated[CallToolResult, DomainChoices]:
        """Discover supported domain schema IDs before creating a KG."""
        current = await access((), Action.LIST_KGS, org_id)
        catalog = services().catalog
        result = DomainChoices(
            items=tuple(
                DomainChoice(id=identifier, name=name)
                for identifier, name in (
                    (catalog.general_profile_id, "general"),
                    (catalog.scientific_profile_id, "scientific"),
                    (catalog.medical_profile_id, "medical"),
                )
            )
        )
        await services().policy.revalidate(current)
        return output(result, "Available domain schemas.")

    @tool("get_schema", read=True)
    async def schema(
        kg_id: UUID, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, DomainSchema]:
        """Inspect legal entity types and predicates for this KG."""
        current = await access((kg_id,), Action.READ, org_id)
        result = await schema_for_kg(services().policy, current, kg_id)
        await services().policy.revalidate(current)
        return output(result, "Current KG schema.")

    @tool("delete_kg", destructive=True)
    async def delete(
        kg_id: UUID, command: KGRevisionCommand, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, KGReceipt]:
        """Owner-only recoverable KG deletion; immediately fences reads and publication.

        Original data is retained. Discover deleted KGs using list_kgs(lifecycle='deleted').
        """
        current = await access((kg_id,), Action.CONFIGURE, org_id)
        return output(
            await services().catalog.delete_kg(current, kg_id, **command.model_dump()),
            "KG deleted; retained data can be restored explicitly.",
        )

    @tool("restore_kg", destructive=True)
    async def restore(
        kg_id: UUID, command: KGRevisionCommand, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, KGReceipt]:
        """Owner-only restoration of a recoverably deleted KG, preserving evidence and history."""
        current = await access((kg_id,), Action.CONFIGURE, org_id)
        return output(
            await services().catalog.restore_kg(current, kg_id, **command.model_dump()),
            "KG restored.",
        )

    @tool("upload_file")
    async def upload(
        command: UploadCommand,
        content_base64: Annotated[str, Field(max_length=699052)],
        org_id: UUID | None = None,
    ) -> Annotated[CallToolResult, UploadReceipt]:
        """Upload up to 512 KiB of base64 file bytes, verifying declared hash and length.

        For files up to 50 MiB, stream bytes with the same bearer credential to
        POST /api/v1/kgs/{kg_id}/uploads, then pass upload_id to add_material/add_folder.
        The command fields are HTTP query parameters. Never pass filesystem paths.
        """
        current = await access(
            (command.kg_id,),
            Action.REPLACE if command.target_material_id else Action.INGEST,
            org_id,
        )
        try:
            content = base64.b64decode(content_base64, validate=True)
        except (ValueError, binascii.Error):
            raise DomainError(
                ErrorCode.VALIDATION_ERROR, "File content must be valid base64."
            ) from None
        if len(content) > 512 * 1024:
            raise DomainError(
                ErrorCode.QUOTA_EXCEEDED, "Use streaming HTTP for files over 512 KiB."
            )

        async def stream() -> AsyncIterable[bytes]:
            yield content

        return output(
            await services().materials.upload(current, command, stream()),
            "Upload completed; submit its upload_id for ingestion.",
        )

    @tool("add_folder", external=True)
    async def folder(
        kg_id: UUID, command: FolderCommand, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, FolderResult]:
        """Submit 1–100 relative file paths with completed upload IDs or inline text.

        Each entry adds a material or explicitly replaces material_id/expected_active_version.
        Files share this KG's graph. Per-file failures do not block other entries.
        Reuse the same key and manifest to resume; changed input requires a new key.
        """
        current = await access((kg_id,), Action.INGEST, org_id)
        return output(
            await FolderIngestion(services().materials).submit(current, kg_id, command),
            "Folder submitted; inspect each item and poll accepted job IDs.",
        )

    @tool("list_managed_materials", read=True)
    async def managed(
        kg_id: UUID,
        state: ManagementState = "pending",
        page: PageRequest = DEFAULT_PAGE,
        org_id: UUID | None = None,
    ) -> Annotated[CallToolResult, ManagedMaterialPage]:
        """Find pending/removed sources for processing, deletion or restoration."""
        return output(
            await services().management.list(
                await access((kg_id,), Action.REPLACE, org_id), kg_id, state, page
            ),
            "Current managed sources.",
        )

    @tool("update_material_metadata", destructive=True)
    async def metadata(
        command: UpdateMaterialMetadata, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, MaterialMetadataReceipt]:
        """Edit title, tags, author, source or language with an expected metadata revision."""
        return output(
            await services().lifecycle.update_metadata(
                await access((command.kg_id,), Action.REPLACE, org_id), command
            ),
            "Metadata updated.",
        )

    @tool("purge_material", destructive=True)
    async def purge(
        command: PurgeMaterial, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, PurgeReceipt]:
        """Owner-only permanent source purge after retention and ownership checks."""
        return output(
            await services().lifecycle.purge(
                await access((command.kg_id,), Action.PURGE, org_id), command
            ),
            "Purge accepted.",
        )

    @tool("get_material_acl", read=True)
    async def acl(
        kg_id: UUID, material_id: UUID, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, MaterialACLView]:
        """Read current restricted-source grants and policy revision; owner only."""
        return output(
            await services().material_grants.get(
                await access((kg_id,), Action.MANAGE_GRANTS, org_id), kg_id, material_id
            ),
            "Current source grants.",
        )

    @tool("set_material_acl", destructive=True)
    async def set_acl(
        command: MaterialACLChange, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, PolicyReceipt]:
        """Set source grants with expected policy revision; cannot grant KG membership."""
        return output(
            await services().material_grants.set(
                await access((command.kg_id,), Action.MANAGE_GRANTS, org_id), command
            ),
            "Source access updated.",
        )

    @tool("list_images", read=True)
    async def images(
        kg_id: UUID, material_id: UUID, version_id: UUID, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, ImagePage]:
        """List separately preserved images and protected original/preview URLs."""
        current = await access((kg_id,), Action.READ, org_id)
        return output(
            ImagePage(
                items=await ImageSources(services().queue).list(
                    current, kg_id, material_id, version_id
                )
            ),
            "Visible image sources.",
        )

    @tool("read_source_file", read=True)
    async def source_file(
        kg_id: UUID,
        material_id: UUID,
        version_id: UUID,
        image_id: UUID | None = None,
        preview: bool = False,
        offset: Offset = 0,
        limit: ReadLimit = 512 * 1024,
        org_id: UUID | None = None,
    ) -> Annotated[CallToolResult, FileChunk]:
        """Read bounded base64 bytes of an authorized original or its separate image.

        Follow next_offset until eof. Prefer protected HTTP downloads for large files.
        """
        current = await access((kg_id,), Action.READ, org_id)
        if preview and image_id is None:
            raise DomainError(ErrorCode.VALIDATION_ERROR, "An image preview requires image_id.")
        stream = (
            services().materials.original(current, kg_id, material_id, version_id)
            if image_id is None
            else ImageSources(services().queue).stream(
                current, kg_id, material_id, version_id, image_id, preview=preview
            )
        )
        return output(await file_chunk(stream, offset, limit), "Authorized source bytes.")

    @tool("list_jobs", read=True)
    async def jobs(
        kg_id: UUID, page: PageRequest = DEFAULT_PAGE, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, JobPage]:
        """Discover visible durable jobs, progress, warnings and safe error diagnostics."""
        return output(
            await services().job_inspection.list(
                await access((kg_id,), Action.READ, org_id), kg_id, page
            ),
            "Current jobs.",
        )

    @tool("retry_job", external=True)
    async def retry(
        kg_id: UUID, job_id: UUID, idempotency_key: IdempotencyKey, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, JobReceipt]:
        """Explicitly retry a failed/cancelled job; current source/job authorization applies."""
        return output(
            await services().queue.retry(
                await access((kg_id,), Action.READ, org_id), job_id, idempotency_key=idempotency_key
            ),
            "Job retry accepted.",
        )

    @tool("cancel_job", destructive=True)
    async def cancel(
        kg_id: UUID, job_id: UUID, idempotency_key: IdempotencyKey, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, JobReceipt]:
        """Cancel durable work explicitly; cancelling an MCP request alone does not cancel a job."""
        return output(
            await services().queue.cancel(
                await access((kg_id,), Action.READ, org_id), job_id, idempotency_key=idempotency_key
            ),
            "Job cancellation accepted.",
        )

    @tool("batch", destructive=True, external=True)
    async def batch(
        kg_id: UUID, command: BatchCommand, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, BatchResult]:
        """Batch delete, restore, process, retry or cancel up to 100 same-KG targets.

        Material targets bind revisions and processing binds active version_id.
        Each target returns its own outcome; use get_batch to inspect/resume safely.
        """
        return output(
            await services().batch.execute(
                await access((kg_id,), services().batch.action(command.operation), org_id),
                kg_id,
                command,
            ),
            "Batch processed; inspect per-item outcomes.",
        )

    @tool("get_batch", read=True)
    async def batch_result(
        kg_id: UUID, batch_id: UUID, org_id: UUID | None = None
    ) -> Annotated[CallToolResult, BatchResult]:
        """Read an initiating actor's current-authority batch result."""
        return output(
            await services().batch.get(
                await access((kg_id,), Action.READ, org_id), kg_id, batch_id
            ),
            "Current batch result.",
        )

    @tool("get_graph", read=True)
    async def graph(
        kg_id: UUID,
        filters: SearchFilters = DEFAULT_FILTERS,
        page_index: Annotated[int, Field(ge=0, le=999)] = 0,
        org_id: UUID | None = None,
    ) -> Annotated[CallToolResult, GraphOverview]:
        """Browse the KG without seed IDs, up to 200 nodes/400 edges per page."""
        current = await access((kg_id,), Action.READ, org_id)
        view, count = await services().graph.overview(
            current, kg_id, filters, page_index=page_index
        )
        return output(
            GraphOverview(
                view=view,
                total_nodes=count,
                page_index=page_index,
                next_page_index=page_index + 1 if (page_index + 1) * 200 < count else None,
            ),
            f"{count} visible KG nodes.",
        )

    @tool("get_edit_context", read=True)
    async def edit(
        kg_id: UUID,
        target_id: UUID,
        kind: Literal["node", "relation"],
        source_ids: tuple[UUID, ...] = (),
        public: bool = False,
        org_id: UUID | None = None,
    ) -> Annotated[CallToolResult, EditView]:
        """Resolve source scope and expected revision before node/relation editing."""
        current = await access((kg_id,), Action.CURATE, org_id)
        result = await CurationInspection(services().queue).edit(
            current, kg_id, target_id, kind, source_ids=source_ids, public=public
        )
        return output(
            EditView(
                source_ids=result.source_ids,
                public=result.public,
                expected_revision=result.expected_revision,
                node=result.node,
                edges=result.edges,
            ),
            "Current edit scope and revision.",
        )

    @tool("get_citation", read=True)
    async def citation(
        kg_id: UUID,
        citation_id: UUID,
        material_id: UUID | None = None,
        version_id: UUID | None = None,
        include_unverified: bool = False,
        org_id: UUID | None = None,
    ) -> Annotated[CallToolResult, Citation]:
        """Resolve evidence spans, image/model or manual provenance under current access."""
        if version_id is not None and material_id is None:
            raise DomainError(ErrorCode.VALIDATION_ERROR, "A version requires its material ID.")
        current = await access((kg_id,), Action.READ, org_id)
        return output(
            await services().citations.resolve(
                current,
                kg_id,
                citation_id,
                material_id=material_id,
                version_id=version_id,
                include_unverified=include_unverified,
            ),
            "Authorized citation.",
        )
