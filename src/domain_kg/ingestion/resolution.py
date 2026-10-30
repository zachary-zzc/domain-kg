"""Scoped canonical resolution. Display fields remain source-owned observations."""

import hashlib
import json
import unicodedata
from typing import TYPE_CHECKING, Any
from uuid import uuid5

from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.engine.source_mapping import engine_name
from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.models import AssertionRecord, EntityRecord, SupportRecord
from domain_kg.graph.schema import schema_for_kg
from domain_kg.identity.models import AccessContext, Action
from domain_kg.identity.policy import Policy
from domain_kg.ingestion.extraction import validate_extraction
from domain_kg.ingestion.models import ExtractionBatch, ParsedChunk, ParsedDocument, span_status
from domain_kg.ingestion.resolved import ResolvedAssertion, ResolvedBatch, ResolvedEntity
from domain_kg.jobs.models import JobArtifact
from domain_kg.materials.models import BlobRef, ReprocessReadPermit
from domain_kg.materials.repository import MaterialRepository
from domain_kg.models import ScopedRef, SourceVersion
from domain_kg.storage.blobs import BlobStore
from domain_kg.storage.integrity import COLLECTIONS, CanonicalRecord
from domain_kg.storage.router import KGStoreRouter, RoutedStores

if TYPE_CHECKING:
    from domain_kg.ingestion.delta import AcceptedDelta


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def normalized(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip()


class ResolutionService:
    def __init__(self, policy: Policy, router: KGStoreRouter) -> None:
        self.policy, self.router, self.database = policy, router, policy.database
        self.transactions = MaterialRepository(policy)

    async def _readable(
        self,
        access: AccessContext,
        batch: ExtractionBatch,
        restoration: ReprocessReadPermit | None,
        session: AsyncClientSession | None = None,
    ) -> None:
        if restoration is not None:
            from domain_kg.jobs.queue import JobQueue
            from domain_kg.materials.restoration import authorize_restore_read

            await authorize_restore_read(
                JobQueue(self.policy, self.router),
                access,
                SourceVersion(**{key: getattr(batch, key) for key in SourceVersion.model_fields}),
                restoration,
                session,
            )
        elif not await self.policy.material_visible(
            access, batch.kg_id, batch.material_id, session=session
        ):
            raise inaccessible_reference()

    async def _chunk(
        self,
        access: AccessContext,
        batch: ExtractionBatch,
        stores: RoutedStores,
        restoration: ReprocessReadPermit | None,
    ) -> ParsedChunk:
        reference = ScopedRef(org_id=batch.org_id, kg_id=batch.kg_id, id=batch.version_id)
        version = await stores.authority.get_material_version(reference)
        if version is None or version.material.id != batch.material_id:
            raise inaccessible_reference()
        await self._readable(access, batch, restoration)
        ns = {"org_id": str(batch.org_id), "kg_id": str(batch.kg_id)}
        raw = await stores.authority._database.source_chunks.find_one(
            ns
            | {
                "id": str(batch.chunk_id),
                "version_id": str(batch.version_id),
                "material_id": str(batch.material_id),
            }
        )
        original = await stores.authority._database.version_blobs.find_one(
            ns | {"id": str(batch.version_id), "material_id": str(batch.material_id)}
        )
        artifact = (
            await self.database.job_artifacts.find_one(
                ns
                | {
                    "id": raw["artifact_id"],
                    "source_version_id": str(batch.version_id),
                    "stage": "chunking",
                }
            )
            if raw
            else None
        )
        if raw is None or original is None or artifact is None:
            raise inaccessible_reference()
        owned = JobArtifact.model_validate(
            {key: value for key, value in artifact.items() if key != "_id"}
        )
        if owned.blob.material_id != batch.material_id:
            raise inaccessible_reference()
        origin = await self.database.jobs.find_one(
            ns
            | {
                "id": str(owned.job_id),
                "target_id": str(batch.version_id),
                "material_id": str(batch.material_id),
            }
        )
        if origin is None:
            raise inaccessible_reference()
        data = bytearray()
        async for part in BlobStore(stores.blobs).open(owned.blob):
            data.extend(part)
        document = ParsedDocument.model_validate_json(bytes(data))
        if document.blob != BlobRef.model_validate(original["blob"]):
            raise inaccessible_reference()
        chunk = next((item for item in document.chunks if item.id == batch.chunk_id), None)
        if (
            chunk is None
            or chunk
            != ParsedChunk.model_validate({key: raw[key] for key in ParsedChunk.model_fields})
            or hashlib.sha256(chunk.text.encode()).hexdigest() != raw["text_sha256"]
        ):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "The source chunk does not match its artifact."
            )
        return chunk

    async def resolve_entities(
        self,
        access: AccessContext,
        batch: ExtractionBatch,
        *,
        predecessor: "AcceptedDelta | None" = None,
        restoration: ReprocessReadPermit | None = None,
    ) -> ResolvedBatch:
        batch = ExtractionBatch.model_validate(batch)
        current = await self.policy.revalidate(access, action=Action.INGEST)
        if batch.org_id != current.org_id or batch.kg_id not in current.kg_ids:
            raise inaccessible_reference()
        stores = await self.router.for_kg(current, batch.kg_id)
        chunk = await self._chunk(current, batch, stores, restoration)
        schema = await schema_for_kg(self.policy, current, batch.kg_id)
        batch = validate_extraction(chunk, schema, batch)
        lineage = predecessor.entity_lineage(batch, chunk) if predecessor else {}
        namespace: dict[str, Any] = {"org_id": batch.org_id, "kg_id": batch.kg_id}
        digest = fingerprint(batch.model_dump(mode="json"))
        extraction_id = uuid5(batch.version_id, digest)
        entities: list[ResolvedEntity] = []
        local: dict[str, ScopedRef] = {}
        for entity in batch.entities:
            if entity.identifier:
                identity = f"identifier/{entity.entity_type}/{normalized(entity.identifier)}"
            else:
                # Mere equal names do not establish identity across documents or
                # separate mentions. Curation can explicitly merge them later.
                identity = f"mention/{batch.version_id}/{entity.entity_type}/" + fingerprint(
                    {
                        "name": normalized(entity.name),
                        "description": normalized(entity.description),
                        "spans": sorted(
                            (str(span.location.block_id), span.start, span.end, span.quote)
                            for span in entity.spans
                        ),
                    }
                )
            reference = ScopedRef(
                **namespace, id=uuid5(batch.org_id, f"{batch.kg_id}/entity/{identity}")
            )
            if entity.identifier is None and entity.local_id in lineage:
                reference = lineage[entity.local_id]
                if (reference.org_id, reference.kg_id) != (batch.org_id, batch.kg_id):
                    raise inaccessible_reference()
            local[entity.local_id] = reference
            observation_id = uuid5(
                batch.version_id,
                fingerprint(
                    {
                        "entity": str(reference.id),
                        "data": entity.model_dump(mode="json"),
                        "profile": str(batch.provider_profile_id),
                        "schema": str(schema.id),
                        "revision": schema.revision,
                        "chunk": str(batch.chunk_id),
                    }
                ),
            )
            entities.append(
                ResolvedEntity(
                    **namespace,
                    material_id=batch.material_id,
                    version_id=batch.version_id,
                    id=observation_id,
                    chunk_id=batch.chunk_id,
                    entity=reference,
                    **entity.model_dump(exclude={"local_id"}),
                )
            )
        assertions: list[ResolvedAssertion] = []
        for assertion in batch.assertions:
            qualifiers = tuple(
                sorted((item.name, normalized(item.value)) for item in assertion.qualifiers)
            )
            identity = fingerprint(
                {
                    "subject": str(local[assertion.subject].id),
                    "target": str(local[assertion.target].id),
                    "predicate": assertion.predicate,
                    "polarity": assertion.polarity,
                    "qualifiers": qualifiers,
                }
            )
            fact = AssertionRecord(
                **namespace,
                id=uuid5(batch.kg_id, f"assertion/{identity}"),
                subject=local[assertion.subject],
                target=local[assertion.target],
                predicate=assertion.predicate,
                polarity=assertion.polarity,
                qualifiers=qualifiers,
            )
            spans = tuple(dict.fromkeys(assertion.spans))
            support_identity = fingerprint(
                {
                    "assertion": str(fact.id),
                    "spans": [span.model_dump(mode="json") for span in spans],
                    "confidence": assertion.confidence,
                    "schema": str(schema.id),
                    "revision": schema.revision,
                    "profile": str(batch.provider_profile_id),
                }
            )
            support = SupportRecord(
                **namespace,
                id=uuid5(batch.version_id, f"support/{support_identity}"),
                assertion=ScopedRef(**namespace, id=fact.id),
                version=ScopedRef(**namespace, id=batch.version_id),
                status=span_status(assertion.spans),
                spans=spans,
                confidence=assertion.confidence,
            )
            observations = {
                proposal.local_id: item
                for proposal, item in zip(batch.entities, entities, strict=True)
            }
            assertions.append(
                ResolvedAssertion(
                    assertion=fact,
                    support=support,
                    subject_observation_id=observations[assertion.subject].id,
                    target_observation_id=observations[assertion.target].id,
                )
            )
        result = ResolvedBatch(
            **namespace,
            material_id=batch.material_id,
            version_id=batch.version_id,
            extraction_id=extraction_id,
            chunk_id=batch.chunk_id,
            schema_id=schema.id,
            schema_revision=schema.revision,
            provider_profile_id=batch.provider_profile_id,
            entities=tuple(entities),
            assertions=tuple(assertions),
            warnings=batch.warnings,
        )
        database = stores.authority._database
        for name in ("observations", "engine_names"):
            await database[name].create_index([("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True)
            await database[name].create_index([("org_id", 1), ("kg_id", 1), ("version_id", 1)])
        await database.engine_names.create_index(
            [("org_id", 1), ("kg_id", 1), ("extraction_id", 1), ("engine_key", 1)], unique=True
        )

        async def canonical(record: CanonicalRecord, session: AsyncClientSession) -> None:
            collection = database[COLLECTIONS[type(record)]]
            old = await collection.find_one(stores.authority._query(record.id), session=session)
            if old:
                if (
                    type(record).model_validate(
                        {key: value for key, value in old.items() if key != "_id"}
                    )
                    != record
                ):
                    raise DomainError(
                        ErrorCode.VERSION_CONFLICT, "Canonical source record changed."
                    )
            else:
                await stores.authority.insert(record, session=session)

        async def immutable(
            collection: str, value: dict[str, Any], session: AsyncClientSession
        ) -> None:
            key = {field: value[field] for field in ("org_id", "kg_id", "id")}
            previous = await database[collection].find_one(key, session=session)
            if previous:
                if {field: previous.get(field) for field in value} != value:
                    raise DomainError(
                        ErrorCode.VERSION_CONFLICT, "Immutable source observation changed."
                    )
            else:
                await database[collection].insert_one(
                    value | {"_id": f"{batch.org_id}:{batch.kg_id}:{value['id']}"}, session=session
                )

        async def commit(session: AsyncClientSession) -> None:
            await self.transactions.fence(current, Action.INGEST, session)
            await self._readable(current, batch, restoration, session)
            await stores.authority.require_version(
                ScopedRef(**namespace, id=batch.version_id), session=session
            )
            # Custom schema changes also write the KG row; touch it so concurrent
            # changes conflict instead of committing obsolete schema validation.
            current_schema = await schema_for_kg(self.policy, current, batch.kg_id, session=session)
            if current_schema != schema:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The domain schema changed.")
            await self.database.kgs.update_one(
                {"org_id": str(batch.org_id), "id": str(batch.kg_id)},
                {"$inc": {"resolution_epoch": 1}},
                session=session,
            )
            for entity, proposal in zip(entities, batch.entities, strict=True):
                await canonical(EntityRecord(**namespace, id=entity.entity.id), session)
                await immutable("observations", entity.model_dump(mode="json"), session)
                await immutable(
                    "engine_names",
                    {
                        "org_id": str(batch.org_id),
                        "kg_id": str(batch.kg_id),
                        "id": str(uuid5(extraction_id, proposal.local_id)),
                        "extraction_id": str(extraction_id),
                        "engine_key": engine_name(chunk, proposal.local_id),
                        "entity": entity.entity.model_dump(mode="json"),
                        "version_id": str(batch.version_id),
                        "chunk_id": str(batch.chunk_id),
                    },
                    session,
                )
            for item in assertions:
                await canonical(item.assertion, session)
                await canonical(item.support, session)

        await self.transactions.transaction(commit)
        await self.policy.revalidate(current)
        await self._readable(current, batch, restoration)
        return result
