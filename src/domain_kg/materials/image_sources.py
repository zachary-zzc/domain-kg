"""Independent image sources inherit the accepted parent's ACL and retention fence."""

from collections.abc import AsyncGenerator
from uuid import UUID

from domain_kg.errors import inaccessible_reference
from domain_kg.graph.service import AcceptedMetadata
from domain_kg.identity.models import AccessContext
from domain_kg.ingestion.models import ParsedDocument
from domain_kg.jobs.queue import JobQueue
from domain_kg.materials.inspection_models import ImageSourceView
from domain_kg.models import SourceVersion
from domain_kg.retrieval.filters import EligibilityResolver
from domain_kg.retrieval.models import SearchFilters, SearchRequest
from domain_kg.revisions.journal import read_artifact
from domain_kg.revisions.models import ReadScope
from domain_kg.revisions.projections import EmbeddedDocument
from domain_kg.revisions.publication import accepted_document
from domain_kg.storage.blobs import BlobStore


class ImageSources:
    def __init__(self, queue: JobQueue) -> None:
        self.queue = queue
        self.resolver = EligibilityResolver(queue.policy, AcceptedMetadata(queue))

    async def _open(
        self, access: AccessContext, kg: UUID, material: UUID, version: UUID
    ) -> tuple[ReadScope, ParsedDocument]:
        scope = await self.resolver.resolve(
            access,
            SearchRequest(
                kg_ids=(kg,),
                query="image source",
                filters=SearchFilters(
                    material_ids=(material,),
                    version_id=version,
                    support_statuses=(
                        "verified",
                        "model_interpreted",
                        "manual",
                        "imported_unverified",
                    ),
                ),
            ),
            kg,
        )
        source = next(
            (
                s
                for s in scope.visible_sources
                if s.material_id == material and s.version_id == version
            ),
            None,
        )
        if source is None:
            raise inaccessible_reference()
        binding = next((b for b in scope.projections if b.source == source), None)
        if binding is None:
            raise inaccessible_reference()
        embedded, _, _ = await accepted_document(self.queue, scope.access, binding)
        if not isinstance(embedded, EmbeddedDocument):
            raise inaccessible_reference()
        parsed = ParsedDocument.model_validate_json(
            await read_artifact(self.queue, scope.access, embedded.chunk_checkpoint, source)
        )
        if any(getattr(parsed.blob, f) != getattr(source, f) for f in SourceVersion.model_fields):
            raise inaccessible_reference()
        await self.resolver.revalidate(scope)
        return scope, parsed

    async def list(
        self, access: AccessContext, kg: UUID, material: UUID, version: UUID
    ) -> tuple[ImageSourceView, ...]:
        scope, document = await self._open(access, kg, material, version)
        prefix = f"/api/v1/kgs/{kg}/materials/{material}/versions/{version}/images/"
        images = tuple(
            ImageSourceView(
                id=i.id,
                media_type=i.media_type,
                sha256=i.sha256,
                size_bytes=i.size_bytes,
                width=i.width,
                height=i.height,
                origins=i.origins,
                original_url=prefix + str(i.id),
                preview_url=prefix + str(i.id) + "/preview",
            )
            for i in document.images
        )
        await self.resolver.revalidate(scope)
        return images

    async def stream(
        self,
        access: AccessContext,
        kg: UUID,
        material: UUID,
        version: UUID,
        image: UUID,
        *,
        preview: bool = False,
    ) -> AsyncGenerator[bytes, None]:
        scope, document = await self._open(access, kg, material, version)
        owned = next((i for i in document.images if i.id == image), None)
        blob = (owned.preview_blob if preview else owned.blob) if owned else None
        if blob is None:
            raise inaccessible_reference()
        stores = await self.queue.router.for_sources(scope.access, kg)
        async for part in BlobStore(stores.blobs).open(blob):
            await self.resolver.revalidate(scope)
            yield part
        await self.resolver.revalidate(scope)
