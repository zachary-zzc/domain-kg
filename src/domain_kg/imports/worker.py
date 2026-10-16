"""Dedicated import pipeline: owned snapshot decoding and embeddings, never model extraction."""

import hashlib
from typing import Any
from uuid import UUID

from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.models import EntityRecord
from domain_kg.identity.models import AccessContext, Action
from domain_kg.imports.authority import AdmittedImport, load_admitted
from domain_kg.imports.build import build_import, snapshot_document
from domain_kg.imports.legacy import MAX_BYTES, parse_legacy
from domain_kg.imports.prepared import PreparedImport
from domain_kg.ingestion.chunking import VERSION
from domain_kg.ingestion.pipeline import PipelineStep, publication_steps
from domain_kg.ingestion.repository import SourceRepository
from domain_kg.jobs.models import Checkpoint, LeasedJob, PreparationStage
from domain_kg.jobs.queue import JobQueue
from domain_kg.models import Namespace, ScopedRef
from domain_kg.providers.embeddings import EmbeddingProfile
from domain_kg.providers.service import ProviderService
from domain_kg.revisions.journal import read_artifact
from domain_kg.revisions.projections import EmbeddedDocument, ProjectionRow
from domain_kg.revisions.publication import ProjectionCoordinator
from domain_kg.storage.integrity import COLLECTIONS, CanonicalRecord


class ImportWorker:
    def __init__(self, queue: JobQueue, providers: ProviderService) -> None:
        self.queue, self.providers = queue, providers
        self.sources = SourceRepository(queue)

    async def context(self, job: LeasedJob) -> tuple[AccessContext, AdmittedImport]:
        access = await self.queue.authorize(job)
        raw = await self.queue._current(job)
        return access, await load_admitted(self.queue, access, job, UUID(raw["material_id"]))

    async def input_hash(self, job: LeasedJob, stage: str) -> str:
        access, admitted = await self.context(job)
        content = (
            "legacy-import-pipeline-v1/"
            + VERSION
            + "/"
            + admitted.origin.model_dump_json()
            + admitted.domain_schema.model_dump_json()
            + "/"
            + stage
        )
        if stage in ("resolving", "embedding"):
            selected, profile = await self.profile(access, admitted)
            content += profile.model_dump_json() + str(selected["revision"])
        return hashlib.sha256(content.encode()).hexdigest()

    async def profile(
        self, access: AccessContext, admitted: AdmittedImport
    ) -> tuple[dict[str, Any], EmbeddingProfile]:
        selected = await self.providers.selected(access, admitted.origin.kg_id)
        ns = Namespace(org_id=admitted.origin.org_id, kg_id=admitted.origin.kg_id)
        configured, _ = self.providers.registry.resolve_embedding(
            UUID(selected["profile_id"]), ns, restricted=True
        )
        if configured.revision != selected["profile_revision"]:
            raise DomainError(ErrorCode.VERSION_CONFLICT, "The import embedding profile changed.")
        return selected, EmbeddingProfile(
            **ns.model_dump(),
            id=configured.id,
            revision=configured.revision,
            model=configured.embedding_model,
            dimensions=configured.embedding_dimensions,
            space_id=selected["embedding_space_id"],
        )

    async def original(self, job: LeasedJob) -> bytes:
        _, admitted = await self.context(job)
        blob, store = await self.sources.original(job)
        if blob.size_bytes > MAX_BYTES or blob.sha256 != admitted.graph.raw_sha256:
            raise DomainError(ErrorCode.EVIDENCE_VALIDATION_FAILED, "The admitted export differs.")
        parts = []
        async for part in store.open(blob):
            parts.append(part)
        await self.queue.authorize(job)
        return b"".join(parts)

    async def required(self, job: LeasedJob, stage: PreparationStage) -> Checkpoint:
        checkpoint = await self.queue.load_checkpoint(job, stage, await self.input_hash(job, stage))
        if checkpoint is None:
            raise DomainError(
                ErrorCode.REVISION_NOT_READY, "The preceding import stage is not ready."
            )
        return checkpoint

    async def execute(self, job: LeasedJob, stage: PreparationStage) -> Checkpoint:
        access, admitted = await self.context(job)
        wanted = await self.input_hash(job, stage)
        if stage == "validating":
            await self.original(job)
            blob, _ = await self.sources.original(job)
            body = blob.model_dump_json().encode()
        elif stage in ("parsing", "chunking"):
            blob, _ = await self.sources.original(job)
            if stage == "parsing":
                document = snapshot_document(blob, await self.original(job))
            else:
                document = await self.sources.read(job, await self.required(job, "parsing"))
            checkpoint = await self.queue.store_artifact(
                job, stage, wanted, document.model_dump_json().encode()
            )
            await self.sources.persist(job, document, checkpoint, chunked=stage == "chunking")
            return checkpoint
        elif stage == "extracting":
            # Decode the immutable export; no ExtractionArtifact or provider provenance is invented.
            data = await self.original(job)
            raw = await self.queue.database.import_plans.find_one(
                {
                    "org_id": str(job.org_id),
                    "kg_id": str(job.kg_id),
                    "id": str(admitted.origin.plan_id),
                }
            )
            if raw is None:
                raise inaccessible_reference()
            graph = parse_legacy(
                admitted.graph.namespace,
                raw["plan"]["source"]["format"],
                data,
                origin=admitted.origin.origin,
            )
            if graph != admitted.graph:
                raise DomainError(
                    ErrorCode.EVIDENCE_VALIDATION_FAILED, "The decoded export differs."
                )
            body = graph.model_dump_json().encode()
        elif stage == "resolving":
            graph_checkpoint = await self.required(job, "extracting")
            source = admitted.origin
            from domain_kg.imports.legacy_models import LegacyGraph
            from domain_kg.models import SourceVersion

            owner = SourceVersion(
                **{field: getattr(source, field) for field in SourceVersion.model_fields}
            )
            graph = LegacyGraph.model_validate_json(
                await read_artifact(self.queue, access, graph_checkpoint, owner)
            )
            chunk_checkpoint = await self.required(job, "chunking")
            document = await self.sources.read(job, chunk_checkpoint)
            prepared = build_import(
                graph, admitted.origin, document, admitted.domain_schema, chunk_checkpoint
            )
            await self.persist(job, prepared)
            body = prepared.model_dump_json().encode()
        else:
            raise DomainError(ErrorCode.VALIDATION_ERROR, "Unknown import preparation stage.")
        if await self.input_hash(job, stage) != wanted:
            raise DomainError(ErrorCode.VERSION_CONFLICT, "The import dependencies changed.")
        return await self.queue.store_artifact(job, stage, wanted, body)

    async def persist(self, job: LeasedJob, prepared: PreparedImport) -> None:
        access, admitted = await self.context(job)
        if (
            prepared.import_origin != admitted.origin
            or prepared.domain_schema != admitted.domain_schema
        ):
            raise inaccessible_reference()
        stores = await self.queue.router.for_kg(access, job.kg_id)
        database = stores.authority._database
        for name in ("observations", "import_id_mappings"):
            await database[name].create_index([("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True)
            await database[name].create_index([("org_id", 1), ("kg_id", 1), ("version_id", 1)])

        async def canonical(record: CanonicalRecord, session: AsyncClientSession) -> None:
            collection = database[COLLECTIONS[type(record)]]
            old = await collection.find_one(stores.authority._query(record.id), session=session)
            if old is not None:
                if (
                    type(record).model_validate(
                        {key: value for key, value in old.items() if key != "_id"}
                    )
                    != record
                ):
                    raise DomainError(ErrorCode.VERSION_CONFLICT, "Canonical import data changed.")
            else:
                await stores.authority.insert(record, session=session)

        async def immutable(
            collection: str, value: dict[str, Any], session: AsyncClientSession
        ) -> None:
            key = {field: value[field] for field in ("org_id", "kg_id", "id")}
            old = await database[collection].find_one(key, session=session)
            if old is not None:
                if {field: old.get(field) for field in value} != value:
                    raise DomainError(
                        ErrorCode.VERSION_CONFLICT, "Immutable import mapping changed."
                    )
            else:
                await database[collection].insert_one(value, session=session)

        async def commit(session: AsyncClientSession) -> None:
            current = await self.queue.authorize(job, session)
            await self.queue.transactions.fence(current, Action.INGEST, session)
            await load_admitted(self.queue, current, job, prepared.material_id, session)
            await stores.authority.require_version(
                ScopedRef(org_id=job.org_id, kg_id=job.kg_id, id=job.target_id), session=session
            )
            await self.queue.database.kgs.update_one(
                {"org_id": str(job.org_id), "id": str(job.kg_id)},
                {"$inc": {"resolution_epoch": 1}},
                session=session,
            )
            for row in prepared.rows:
                if row.observation:
                    entity = row.observation
                    await canonical(
                        EntityRecord(org_id=job.org_id, kg_id=job.kg_id, id=entity.entity.id),
                        session,
                    )
                    await immutable("observations", entity.model_dump(mode="json"), session)
            for row in prepared.rows:
                if row.assertion:
                    await canonical(row.assertion.assertion, session)
                    await canonical(row.assertion.support, session)
            from domain_kg.imports.build import owned_id

            for legacy_entity in admitted.graph.entities:
                identifier = owned_id(
                    admitted.origin, "observation", legacy_entity.source_id, legacy_entity.legacy_id
                )
                await immutable(
                    "import_id_mappings",
                    {
                        "org_id": str(job.org_id),
                        "kg_id": str(job.kg_id),
                        "id": str(identifier),
                        "material_id": str(prepared.material_id),
                        "version_id": str(job.target_id),
                        "plan_id": str(admitted.origin.plan_id),
                        "origin": admitted.origin.origin,
                        "legacy_source_id": legacy_entity.source_id,
                        "legacy_id": legacy_entity.legacy_id,
                        "entity": ScopedRef(
                            org_id=job.org_id, kg_id=job.kg_id, id=legacy_entity.entity_id
                        ).model_dump(mode="json"),
                    },
                    session,
                )

        await self.queue.transactions.transaction(commit)

    async def embed(self, job: LeasedJob, resolved: Checkpoint, input_hash: str) -> Checkpoint:
        access, admitted = await self.context(job)
        from domain_kg.models import SourceVersion

        source = SourceVersion(
            **{field: getattr(admitted.origin, field) for field in SourceVersion.model_fields}
        )
        prepared = PreparedImport.model_validate_json(
            await read_artifact(self.queue, access, resolved, source)
        )
        if (
            prepared.import_origin != admitted.origin
            or prepared.domain_schema != admitted.domain_schema
        ):
            raise inaccessible_reference()
        selected, expected = await self.profile(access, admitted)
        await self.providers.authorize_source(access, source)
        profile, vectors = await self.providers.embeddings(
            access,
            Namespace(org_id=job.org_id, kg_id=job.kg_id),
            [row.text for row in prepared.rows],
            source=source,
        )
        if profile != expected or await self.providers.selected(access, job.kg_id) != selected:
            raise DomainError(ErrorCode.VERSION_CONFLICT, "The import embedding selection changed.")
        document = EmbeddedDocument(
            **source.model_dump(),
            chunk_checkpoint=prepared.chunk_checkpoint,
            resolved_checkpoint=resolved,
            profile=profile,
            profile_selection_revision=selected["revision"],
            schema_id=prepared.domain_schema.id,
            schema_revision=prepared.domain_schema.revision,
            import_origin=admitted.origin,
            rows=tuple(
                ProjectionRow(**row.model_dump(), vector=tuple(vector))
                for row, vector in zip(prepared.rows, vectors, strict=True)
            ),
            warnings=prepared.warnings,
        )
        await self.queue.authorize(job)
        return await self.queue.store_artifact(
            job, "embedding", input_hash, document.model_dump_json().encode()
        )


def import_steps(queue: JobQueue, providers: ProviderService) -> tuple[PipelineStep, ...]:
    worker = ImportWorker(queue, providers)

    def step(stage: PreparationStage) -> PipelineStep:
        async def execute(job: LeasedJob) -> Checkpoint:
            return await worker.execute(job, stage)

        async def hash_for(job: LeasedJob) -> str:
            return await worker.input_hash(job, stage)

        return PipelineStep(
            stage=stage,
            input_hash=hashlib.sha256(("legacy/" + stage).encode()).hexdigest(),
            execute=execute,
            input_hash_for=hash_for,
        )

    stages: tuple[PreparationStage, ...] = (
        "validating",
        "parsing",
        "chunking",
        "extracting",
        "resolving",
    )
    preparation = tuple(step(stage) for stage in stages)
    return preparation + publication_steps(
        queue, ProjectionCoordinator(queue, providers), preparation[-1], importing=True
    )
