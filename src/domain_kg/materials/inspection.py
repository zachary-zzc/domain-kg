"""Current policy and publication fences apply before material paging and return."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import AccessContext, Action
from domain_kg.imports.evidence import source_origin
from domain_kg.jobs.queue import JobQueue
from domain_kg.materials.inspection_models import (
    MaterialDetail,
    MaterialPage,
    MaterialSummary,
    MaterialVersionSummary,
)
from domain_kg.materials.models import BlobRef, MaterialMetadata
from domain_kg.models import PageRequest, ScopedRef
from domain_kg.retrieval.models import SearchFilters, SearchRequest
from domain_kg.revisions.models import ReadScope


class MaterialInspection:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database = queue, queue.database
        self.graph = GraphService(queue)
        self.resolver = self.graph.resolver

    async def _summary(self, scope: ReadScope, identifier: UUID) -> MaterialSummary:
        current = scope.access
        ns: dict[str, Any] = {"org_id": str(current.org_id), "kg_id": str(scope.view.kg_id)}
        row = await self.database.material_access.find_one(ns | {"id": str(identifier)})
        if row is None or not await self.queue.policy.material_visible(
            current, scope.view.kg_id, identifier
        ):
            raise inaccessible_reference()
        source = next(
            (item for item in scope.visible_sources if item.material_id == identifier), None
        )
        if source is None:
            raise inaccessible_reference()
        stores = await self.queue.router.for_sources(current, scope.view.kg_id)
        material = await stores.authority._require("materials", ScopedRef(**ns, id=identifier))
        metadata = MaterialMetadata.model_validate(row["metadata"])
        if (
            metadata.material_id != identifier
            or str(metadata.version_id) != row["active_version_id"]
        ):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Material metadata ownership differs."
            )
        return MaterialSummary(
            import_origin=next(
                (
                    binding.import_origin
                    for binding in scope.current_view.projections
                    if binding.source
                    and binding.source.material_id == identifier
                    and str(binding.source.version_id) == row["active_version_id"]
                ),
                None,
            ),
            id=identifier,
            title=material["title"],
            revision=row["revision"],
            active_version_id=row["active_version_id"],
            metadata=metadata,
        )

    async def list(
        self,
        access: AccessContext,
        kg: UUID,
        page: PageRequest,
        filters: SearchFilters | None = None,
    ) -> MaterialPage:
        page = PageRequest.model_validate(page)
        filters = SearchFilters.model_validate(filters or SearchFilters())
        if filters.requires_assertion_support or filters.exclude_entity_ids:
            raise DomainError(
                ErrorCode.UNSUPPORTED_FILTER, "Material catalog accepts material metadata filters."
            )
        current = await self.queue.policy.revalidate(access, action=Action.READ)
        scope = await self.resolver.resolve(
            current, SearchRequest(kg_ids=(kg,), query="material metadata", filters=filters), kg
        )
        if len(scope.visible_sources) > 1000:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Material catalog exceeds its read budget.")
        binding = {
            "org_id": str(current.org_id),
            "kg_id": str(kg),
            "issuer": current.principal.issuer,
            "subject": current.principal.subject,
            "principal_kind": current.principal.kind,
            "policy_revision": current.policy_revision,
            "visibility_revision": scope.visibility_revision,
            "limit": page.limit,
            "manifest_id": str(scope.current_view.manifest_id),
            "generation": str(scope.view.generation),
            "filters": filters.model_dump(mode="json"),
        }
        after = ""
        if page.cursor:
            cursor = await self.database.material_read_cursors.find_one(
                {
                    "_id": hashlib.sha256(page.cursor.encode()).hexdigest(),
                    "expires_at": {"$gt": datetime.now(UTC)},
                }
            )
            if cursor is None or any(cursor.get(key) != value for key, value in binding.items()):
                raise DomainError(ErrorCode.CURSOR_EXPIRED, "The material cursor scope changed.")
            after = cursor["after"]
        selected = sorted(
            (item.material_id for item in scope.visible_sources if str(item.material_id) > after),
            key=str,
        )
        items = tuple([await self._summary(scope, item) for item in selected[: page.limit]])
        await self.resolver.revalidate(scope)
        next_cursor = None
        if len(selected) > page.limit:
            next_cursor = secrets.token_urlsafe(32)
            await self.database.material_read_cursors.create_index(
                "expires_at", expireAfterSeconds=0
            )
            await self.database.material_read_cursors.insert_one(
                binding
                | {
                    "_id": hashlib.sha256(next_cursor.encode()).hexdigest(),
                    "after": str(selected[page.limit - 1]),
                    "expires_at": datetime.now(UTC) + timedelta(minutes=15),
                }
            )
        await self.resolver.revalidate(scope)
        return MaterialPage(items=items, next_cursor=next_cursor)

    async def get(
        self,
        access: AccessContext,
        kg: UUID,
        material: UUID,
        version: UUID | None = None,
        *,
        include_unverified: bool = False,
    ) -> MaterialDetail:
        current = await self.queue.policy.revalidate(access, action=Action.READ)
        filters = SearchFilters(
            material_ids=(material,),
            version_id=version,
            support_statuses=("verified", "model_interpreted", "manual", "imported_unverified")
            if include_unverified
            else ("verified", "model_interpreted", "manual"),
        )
        scope = await self.resolver.resolve(
            current, SearchRequest(kg_ids=(kg,), query="material metadata", filters=filters), kg
        )
        summary = await self._summary(scope, material)
        source = next(item for item in scope.visible_sources if item.material_id == material)
        stores = await self.queue.router.for_sources(current, kg)
        ns: dict[str, Any] = {"org_id": str(current.org_id), "kg_id": str(kg)}
        rows: list[dict[str, Any]] = (
            await stores.authority._database.version_blobs.find(
                ns | {"material_id": str(material), "state": {"$in": ["accepted", "retained"]}}
            )
            .sort("id", 1)
            .limit(101)
            .to_list()
        )
        versions = []
        for row in rows[:100]:
            canonical = await stores.authority._require(
                "material_versions", ScopedRef(**ns, id=row["id"])
            )
            if canonical["material"] != ScopedRef(**ns, id=material).model_dump(mode="json"):
                raise inaccessible_reference()
            blob = BlobRef.model_validate(row["blob"])
            if (blob.org_id, blob.kg_id, blob.material_id, str(blob.version_id)) != (
                current.org_id,
                kg,
                material,
                row["id"],
            ):
                raise inaccessible_reference()
            if not any(
                accepted.material_id == material and str(accepted.version_id) == row["id"]
                for accepted in scope.current_view.sources
            ):
                await self.resolver.views.retained(current, kg, material, blob.version_id)
            versions.append(
                MaterialVersionSummary(
                    id=row["id"],
                    state=row["state"],
                    media_type=blob.media_type,
                    size_bytes=blob.size_bytes,
                    sha256=blob.sha256,
                )
            )
        data = await self.graph.repository.open(scope)
        chunks = sorted(
            (row.chunk for row in data.rows if row.chunk is not None and row.source == source),
            key=lambda chunk: str(chunk.id),
        )
        await self.resolver.revalidate(scope)
        from domain_kg.materials.image_sources import ImageSources

        images = await ImageSources(self.queue).list(current, kg, material, source.version_id)
        await self.resolver.revalidate(scope)
        return MaterialDetail(
            import_origin=source_origin(scope, source),
            material=summary,
            images=images,
            versions=tuple(versions),
            selected_version_id=source.version_id,
            versions_truncated=len(rows) > 100,
            chunks=tuple(chunks[:25]),
            chunks_truncated=len(chunks) > 25,
        )
