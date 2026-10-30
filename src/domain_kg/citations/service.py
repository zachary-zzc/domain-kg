"""Resolve only sealed evidence in the caller's current eligible publication."""

from uuid import UUID, uuid5

from domain_kg.citations.models import Citation
from domain_kg.errors import inaccessible_reference
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import AccessContext
from domain_kg.imports.evidence import source_origin
from domain_kg.jobs.queue import JobQueue
from domain_kg.retrieval.graph import source_citation
from domain_kg.retrieval.models import SearchFilters, SearchRequest


class CitationService:
    def __init__(self, queue: JobQueue) -> None:
        self.graph = GraphService(queue)
        self.queue = queue

    async def resolve(
        self,
        access: AccessContext,
        kg: UUID,
        citation_id: UUID,
        *,
        material_id: UUID | None = None,
        version_id: UUID | None = None,
        include_unverified: bool = False,
    ) -> Citation:
        filters = SearchFilters(
            material_ids=(material_id,) if material_id else (),
            version_id=version_id,
            support_statuses=("verified", "model_interpreted", "manual", "imported_unverified")
            if include_unverified
            else ("verified", "model_interpreted", "manual"),
        )
        scope = await self.graph.resolver.resolve(
            access, SearchRequest(kg_ids=(kg,), query="citation", filters=filters), kg
        )
        data = await self.graph.repository.open(scope)
        resolver_url = f"/api/v1/citations/{citation_id}?kg_id={kg}"
        result = None
        for row in data.rows:
            if row.chunk is None or row.source not in scope.visible_sources:
                continue
            source = row.chunk
            if citation_id != source_citation(
                source.org_id, kg, source.material_id, source.version_id, source.id
            ):
                continue
            resolver_url += f"&material_id={source.material_id}&version_id={source.version_id}"
            origin = source_origin(scope, row.source)
            if origin is not None:
                resolver_url += "&include_unverified=true"
            from domain_kg.materials.image_sources import ImageSources

            image_ids = {span.location.image_id for span in source.spans if span.location.image_id}
            images = (
                await ImageSources(self.queue).list(
                    access, kg, source.material_id, source.version_id
                )
                if image_ids
                else ()
            )
            result = Citation(
                images=tuple(i for i in images if i.id in image_ids),
                id=citation_id,
                kg_id=kg,
                kind="source",
                text=source.text,
                material_id=source.material_id,
                version_id=source.version_id,
                chunk_id=source.id,
                spans=() if origin else source.spans,
                import_origin=origin,
                resolver_url=resolver_url,
                original_url=(
                    f"/api/v1/kgs/{kg}/materials/{source.material_id}"
                    f"/versions/{source.version_id}/original"
                ),
            )
            break
        if result is None:
            for row in data.manual_rows:
                manual = row.manual_node or row.manual_relation
                if manual is None or citation_id != uuid5(
                    row.org_id, f"{kg}/curation/citation/{manual.curation.id}"
                ):
                    continue
                result = Citation(
                    id=citation_id,
                    kg_id=kg,
                    kind="manual",
                    text=row.text,
                    resolver_url=resolver_url,
                    curation_revision_id=manual.curation.id,
                    provenance=manual.provenance,
                    material_id=manual.material_ids[0] if len(manual.material_ids) == 1 else None,
                )
                break
        await self.graph.resolver.revalidate(scope)
        if result is None:
            raise inaccessible_reference()
        return result
