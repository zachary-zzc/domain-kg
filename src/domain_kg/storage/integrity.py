"""Internal scoped integrity boundary. These clients are never transport arguments.

Mongo validators enforce shapes and compound uniqueness. References are checked
by scoped repositories because MongoDB does not supply cross-document foreign keys.
Only staging projections are written here; their presence does not mean acceptance.
"""

import hashlib
import json
from typing import Any
from uuid import UUID

from neo4j import AsyncDriver
from pydantic import ValidationError
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import CollectionInvalid
from qdrant_client import AsyncQdrantClient, models

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.models import AssertionRecord, EntityRecord, SupportRecord
from domain_kg.models import Namespace, ScopedRef
from domain_kg.storage.models import (
    CurationRecord,
    IntegrityIssue,
    MaterialRecord,
    MaterialVersionRecord,
    ProjectionSchema,
)

CanonicalRecord = (
    MaterialRecord
    | MaterialVersionRecord
    | EntityRecord
    | AssertionRecord
    | SupportRecord
    | CurationRecord
)
RECORD_TYPES: dict[str, type[CanonicalRecord]] = {
    "materials": MaterialRecord,
    "material_versions": MaterialVersionRecord,
    "entities": EntityRecord,
    "assertions": AssertionRecord,
    "supports": SupportRecord,
    "curations": CurationRecord,
}
COLLECTIONS = {record_type: name for name, record_type in RECORD_TYPES.items()}


def _reference_schema() -> dict[str, Any]:
    return {
        "bsonType": "object",
        "required": ["org_id", "kg_id", "id"],
        "additionalProperties": False,
        "properties": {
            key: {"bsonType": "string", "pattern": "^[0-9a-f]{8}-[0-9a-f-]{27}$"}
            for key in ("org_id", "kg_id", "id")
        },
    }


async def install_mongo_schema(database: AsyncDatabase[dict[str, Any]]) -> None:
    """Apply idempotently at provisioning, never as an untrusted request operation."""
    existing = await database.list_collection_names()
    properties: dict[str, dict[str, Any]] = {
        "title": {"bsonType": "string", "minLength": 1, "maxLength": 1000},
        "material": _reference_schema(),
        "subject": _reference_schema(),
        "target": _reference_schema(),
        "assertion": _reference_schema(),
        "predicate": {"bsonType": "string", "minLength": 1},
        "version": {"anyOf": [_reference_schema(), {"bsonType": "null"}]},
        "curation": {"anyOf": [_reference_schema(), {"bsonType": "null"}]},
        "status": {"enum": ["verified", "manual", "imported_unverified", "rejected"]},
        "spans": {"bsonType": "array", "items": {"bsonType": "object"}},
        "provenance": {"bsonType": ["object", "null"]},
        "actor": {"bsonType": "string", "minLength": 1},
        "reason": {"bsonType": "string", "minLength": 1},
    }
    for name, record_type in RECORD_TYPES.items():
        fields = record_type.model_fields
        schema: dict[str, Any] = {
            "bsonType": "object",
            "additionalProperties": False,
            "required": ["_id", *[key for key, field in fields.items() if field.is_required()]],
            "properties": {
                "_id": {"bsonType": "string"},
                **{key: value for key, value in _reference_schema()["properties"].items()},
                **{key: properties[key] for key in fields if key in properties},
            },
        }
        if name in existing:
            await database.command(
                {
                    "collMod": name,
                    "validator": {"$jsonSchema": schema},
                    "validationLevel": "strict",
                    "validationAction": "error",
                }
            )
        else:
            try:
                await database.create_collection(
                    name,
                    validator={"$jsonSchema": schema},
                    validationLevel="strict",
                    validationAction="error",
                )
            except CollectionInvalid:
                await database.command(
                    {
                        "collMod": name,
                        "validator": {"$jsonSchema": schema},
                        "validationLevel": "strict",
                        "validationAction": "error",
                    }
                )
        await database[name].create_index(
            [("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True, name="namespace_identity"
        )
        for field in ("material", "subject", "target", "assertion", "version", "curation"):
            if field in fields:
                await database[name].create_index(
                    [("org_id", 1), ("kg_id", 1), (f"{field}.id", 1)], name=f"namespace_{field}"
                )


def _references(record: CanonicalRecord) -> list[tuple[str, str, ScopedRef]]:
    if isinstance(record, MaterialVersionRecord):
        return [("material", "materials", record.material)]
    if isinstance(record, AssertionRecord):
        return [("subject", "entities", record.subject), ("target", "entities", record.target)]
    if isinstance(record, SupportRecord):
        result = [("assertion", "assertions", record.assertion)]
        if record.version is not None:
            result.append(("version", "material_versions", record.version))
        if record.curation is not None:
            result.append(("curation", "curations", record.curation))
        return result
    return []


class NamespaceRepository:
    """Integrity, not policy: Task 4 wraps these with verified AccessContext checks.

    Canonical identities are insert-only here. Later lifecycle operations must use
    Mongo authority transactions and revision fences; direct deletes are not offered.
    """

    def __init__(self, database: AsyncDatabase[dict[str, Any]], namespace: Namespace) -> None:
        self._database = database
        self.namespace = namespace

    def _query(self, identifier: UUID | None = None) -> dict[str, str]:
        query = {"org_id": str(self.namespace.org_id), "kg_id": str(self.namespace.kg_id)}
        if identifier is not None:
            query["id"] = str(identifier)
        return query

    def _check_namespace(self, reference: Namespace) -> None:
        if (reference.org_id, reference.kg_id) != (self.namespace.org_id, self.namespace.kg_id):
            raise inaccessible_reference()

    async def _require(
        self, collection: str, reference: ScopedRef, *, session: AsyncClientSession | None = None
    ) -> dict[str, Any]:
        self._check_namespace(reference)
        record = await self._database[collection].find_one(
            self._query(reference.id), session=session
        )
        if record is None:
            raise inaccessible_reference()
        return record

    async def require_version(
        self, reference: ScopedRef, *, session: AsyncClientSession | None = None
    ) -> None:
        # Follow the full source chain, including corruption of the version's owner.
        raw = await self._require("material_versions", reference, session=session)
        version = MaterialVersionRecord.model_validate(
            {key: value for key, value in raw.items() if key != "_id"}
        )
        await self._require("materials", version.material, session=session)

    async def get_material_version(self, reference: ScopedRef) -> MaterialVersionRecord | None:
        self._check_namespace(reference)
        raw = await self._database.material_versions.find_one(self._query(reference.id))
        if raw is None:
            return None
        await self.require_version(reference)
        return MaterialVersionRecord.model_validate(
            {key: value for key, value in raw.items() if key != "_id"}
        )

    async def insert(
        self, record: CanonicalRecord, *, session: AsyncClientSession | None = None
    ) -> None:
        if type(record) not in COLLECTIONS:
            raise TypeError("unsupported canonical record")
        self._check_namespace(record)
        record = type(record).model_validate(record)
        for _, collection, reference in _references(record):
            await self._require(collection, reference, session=session)
            if collection == "material_versions":
                await self.require_version(reference, session=session)
        data = record.model_dump(mode="json")
        data["_id"] = f"{record.org_id}:{record.kg_id}:{record.id}"
        await self._database[COLLECTIONS[type(record)]].insert_one(data, session=session)

    async def reconcile(self) -> list[IntegrityIssue]:
        issues: list[IntegrityIssue] = []
        for collection, record_type in RECORD_TYPES.items():
            async for raw in self._database[collection].find(self._query()):
                identifier = UUID(raw["id"])
                try:
                    record = record_type.model_validate(
                        {key: value for key, value in raw.items() if key != "_id"}
                    )
                except ValidationError:
                    issues.append(
                        IntegrityIssue(record_id=identifier, code="INVALID_RECORD", field="record")
                    )
                    continue
                for field, target, reference in _references(record):
                    try:
                        await self._require(target, reference)
                    except DomainError:
                        issues.append(
                            IntegrityIssue(
                                record_id=identifier, code="INVALID_REFERENCE", field=field
                            )
                        )
        return issues


class ProjectionStore:
    """Attempt-specific graph/vector staging; Mongo publication is a later service."""

    def __init__(
        self,
        authority: NamespaceRepository,
        graph: AsyncDriver,
        vectors: AsyncQdrantClient,
        collection: str,
    ) -> None:
        self._authority, self._graph, self._vectors = authority, graph, vectors
        self.namespace = authority.namespace
        self._collection = collection  # Provisioned internally, never a request value.

    async def initialize(self, *, dimensions: int) -> None:
        await self._graph.execute_query(
            "CREATE CONSTRAINT scoped_projection_identity IF NOT EXISTS "
            "FOR (n:ScopedProjection) "
            "REQUIRE (n.org_id,n.kg_id,n.projection_id) IS UNIQUE"
        )
        if not await self._vectors.collection_exists(self._collection):
            await self._vectors.create_collection(
                self._collection,
                vectors_config=models.VectorParams(
                    size=dimensions, distance=models.Distance.COSINE
                ),
            )
        for field in ("org_id", "kg_id", "source_version_id", "generation", "attempt_id"):
            await self._vectors.create_payload_index(
                self._collection, field, models.PayloadSchemaType.KEYWORD, wait=True
            )

    async def stage(self, point: ProjectionSchema) -> None:
        self._authority._check_namespace(point)
        point = ProjectionSchema.model_validate(point)
        await self._authority.require_version(point.source)
        payload = {
            "org_id": str(point.org_id),
            "kg_id": str(point.kg_id),
            "id": str(point.id),
            "source_version_id": str(point.source.id),
            "generation": str(point.generation),
            "attempt_id": str(point.attempt_id),
            "collection": self._collection,
        }
        checksum = hashlib.sha256(
            json.dumps(
                point.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        payload["checksum"] = checksum
        records, _, _ = await self._graph.execute_query(
            "MERGE (n:ScopedProjection {org_id:$org,kg_id:$kg,projection_id:$key}) "
            "ON CREATE SET n += $payload RETURN n.checksum AS checksum",
            org=str(point.org_id),
            kg=str(point.kg_id),
            key=str(point.projection_id),
            payload=payload,
        )
        if not records or records[0]["checksum"] != checksum:
            raise DomainError(
                ErrorCode.VERSION_CONFLICT,
                "An immutable staging record already has different content.",
            )
        await self._vectors.upsert(
            self._collection,
            points=[
                models.PointStruct(
                    id=str(point.projection_id), vector=list(point.vector), payload=payload
                )
            ],
            wait=True,
        )

    async def reconcile(self) -> list[IntegrityIssue]:
        issues: list[IntegrityIssue] = []
        offset: UUID | str | int | None = None
        scope_filter = models.Filter(
            must=[
                models.FieldCondition(key=key, match=models.MatchValue(value=str(value)))
                for key, value in (
                    ("org_id", self.namespace.org_id),
                    ("kg_id", self.namespace.kg_id),
                )
            ]
        )
        while True:
            points, offset = await self._vectors.scroll(
                self._collection,
                scroll_filter=scope_filter,
                limit=100,
                offset=offset,
                with_payload=True,
            )
            for point in points:
                payload = point.payload or {}
                identifier = UUID(str(point.id))
                try:
                    identifier = UUID(payload["id"])
                    await self._authority.require_version(
                        ScopedRef(
                            **self.namespace.model_dump(), id=UUID(payload["source_version_id"])
                        )
                    )
                    rows, _, _ = await self._graph.execute_query(
                        "MATCH (n:ScopedProjection {org_id:$org,kg_id:$kg,projection_id:$key}) "
                        "RETURN properties(n) AS payload",
                        org=str(self.namespace.org_id),
                        kg=str(self.namespace.kg_id),
                        key=str(point.id),
                    )
                    if not rows or any(
                        rows[0]["payload"].get(key) != value for key, value in payload.items()
                    ):
                        raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Projection mismatch.")
                except (DomainError, KeyError, ValueError):
                    issues.append(
                        IntegrityIssue(
                            record_id=identifier, code="PROJECTION_INCONSISTENT", field="source"
                        )
                    )
            if offset is None:
                break
        # Check the opposite direction too: a graph write may survive a failed
        # vector write. Bound each page and bind the provisioned collection.
        after = ""
        while True:
            rows, _, _ = await self._graph.execute_query(
                "MATCH (n:ScopedProjection {org_id:$org,kg_id:$kg,collection:$collection}) "
                "WHERE n.projection_id > $after RETURN n.projection_id AS key, n.id AS id "
                "ORDER BY n.projection_id LIMIT 100",
                org=str(self.namespace.org_id),
                kg=str(self.namespace.kg_id),
                collection=self._collection,
                after=after,
            )
            if not rows:
                break
            present = await self._vectors.retrieve(
                self._collection, ids=[row["key"] for row in rows], with_payload=True
            )
            present_ids = {str(point.id) for point in present}
            for row in rows:
                if row["key"] not in present_ids:
                    issues.append(
                        IntegrityIssue(
                            record_id=UUID(row["id"]),
                            code="PROJECTION_INCONSISTENT",
                            field="vector",
                        )
                    )
            after = rows[-1]["key"]
        return issues
