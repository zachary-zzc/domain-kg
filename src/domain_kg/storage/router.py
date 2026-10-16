"""Bounded backend pools and server-selected, namespace-bound store bundles."""

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
from neo4j import AsyncDriver, AsyncGraphDatabase
from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse

from domain_kg.catalog.backends import BackendRegistry, s3_client
from domain_kg.contracts import KGStores
from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext
from domain_kg.identity.policy import Policy
from domain_kg.materials.models import BlobRef
from domain_kg.models import Namespace
from domain_kg.revisions.models import ProjectionReceipt
from domain_kg.storage.integrity import NamespaceRepository, install_mongo_schema
from domain_kg.storage.qdrant import open_qdrant


def projection_checksum(identifiers: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(identifiers)).encode()).hexdigest()


@dataclass(frozen=True)
class GraphBinding:
    namespace: Namespace
    driver: AsyncDriver

    async def verify(self, receipt: ProjectionReceipt) -> bool:
        if (receipt.org_id, receipt.kg_id) != (
            self.namespace.org_id,
            self.namespace.kg_id,
        ) or receipt.backend not in ("graph", "lexical"):
            return False
        rows, _, _ = await self.driver.execute_query(
            "MATCH (n:ScopedProjection {org_id:$org,kg_id:$kg,generation:$generation,"
            "attempt_id:$attempt,manifest_id:$manifest,manifest_hash:$hash}) "
            "RETURN n.projection_id AS id",
            org=str(receipt.org_id),
            kg=str(receipt.kg_id),
            generation=str(receipt.generation),
            attempt=str(receipt.attempt_id),
            manifest=str(receipt.manifest_id),
            hash=receipt.content_hash,
        )
        return (
            len(rows) == receipt.record_count
            and projection_checksum([row["id"] for row in rows]) == receipt.checksum
        )


@dataclass(frozen=True)
class VectorBinding:
    namespace: Namespace
    client: AsyncQdrantClient
    collection: str

    async def verify(self, receipt: ProjectionReceipt) -> bool:
        if (receipt.org_id, receipt.kg_id) != (
            self.namespace.org_id,
            self.namespace.kg_id,
        ) or receipt.backend != "vector":
            return False
        conditions = {
            "org_id": str(receipt.org_id),
            "kg_id": str(receipt.kg_id),
            "generation": str(receipt.generation),
            "attempt_id": str(receipt.attempt_id),
            "manifest_id": str(receipt.manifest_id),
            "manifest_hash": receipt.content_hash,
        }
        query = models.Filter(
            must=[
                models.FieldCondition(key=key, match=models.MatchValue(value=value))
                for key, value in conditions.items()
            ]
        )
        offset: UUID | int | str | None = None
        identifiers: list[str] = []
        while True:
            points, offset = await self.client.scroll(
                self.collection, scroll_filter=query, offset=offset, limit=100, with_payload=False
            )
            identifiers.extend(str(point.id) for point in points)
            if offset is None:
                break
        return (
            len(identifiers) == receipt.record_count
            and projection_checksum(identifiers) == receipt.checksum
        )


@dataclass(frozen=True)
class BlobBinding:
    namespace: Namespace
    client: Any
    bucket: str

    async def get(self, reference: BlobRef) -> bytes:
        if (reference.org_id, reference.kg_id) != (self.namespace.org_id, self.namespace.kg_id):
            raise inaccessible_reference()
        key = "/".join(
            str(value)
            for value in (
                reference.org_id,
                reference.kg_id,
                reference.material_id,
                reference.version_id,
                reference.object_id,
            )
        )
        response = await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=key)
        try:
            data: bytes = await asyncio.to_thread(response["Body"].read, reference.size_bytes + 1)
        finally:
            response["Body"].close()
        if (
            len(data) != reference.size_bytes
            or hashlib.sha256(data).hexdigest() != reference.sha256
        ):
            raise DomainError(
                ErrorCode.EVIDENCE_VALIDATION_FAILED,
                "The original object's integrity check failed.",
            )
        return data


@dataclass(frozen=True)
class RoutedStores(KGStores):
    authority: NamespaceRepository
    graph: GraphBinding
    vectors: VectorBinding
    blobs: BlobBinding


class KGStoreRouter:
    def __init__(
        self, policy: Policy, registry: BackendRegistry, *, dimensions: int, max_backends: int = 4
    ) -> None:
        self.policy, self.registry, self.dimensions = policy, registry, dimensions
        self.max_backends = max_backends
        self._graphs: dict[UUID, AsyncDriver] = {}
        self._vectors: dict[UUID, AsyncQdrantClient] = {}
        self._blobs: dict[UUID, Any] = {}
        self._lock = asyncio.Lock()

    async def _bind(self, access: AccessContext, kg_id: UUID) -> RoutedStores:
        current = await self.policy.revalidate(access)
        if kg_id not in current.kg_ids:
            raise inaccessible_reference()
        kg = await self.policy.database.kgs.find_one(
            {"org_id": str(current.org_id), "id": str(kg_id)}
        )
        if kg is None:
            raise inaccessible_reference()
        profile = await self.registry.profile(UUID(kg["storage_profile_id"]), current.org_id)
        mongo = await self.registry.connection(profile.mongo_id, "mongo")
        graph = await self.registry.connection(profile.neo4j_id, "neo4j")
        vector = await self.registry.connection(profile.qdrant_id, "qdrant")
        blob = await self.registry.connection(profile.object_store_id, "s3")
        async with self._lock:
            for identifier, pool in (
                (profile.neo4j_id, self._graphs),
                (profile.qdrant_id, self._vectors),
                (profile.object_store_id, self._blobs),
            ):
                if identifier not in pool and len(pool) >= self.max_backends:
                    raise DomainError(
                        ErrorCode.QUOTA_EXCEEDED, "The configured backend pool limit was reached."
                    )
            if profile.neo4j_id not in self._graphs:
                self._graphs[profile.neo4j_id] = AsyncGraphDatabase.driver(
                    graph.endpoint,
                    auth=(graph.username, graph.credential.get_secret_value()),
                    max_connection_pool_size=2,
                    connection_timeout=5,
                    connection_acquisition_timeout=5,
                    max_transaction_retry_time=5,
                )
            if profile.qdrant_id not in self._vectors:
                self._vectors[profile.qdrant_id] = await open_qdrant(
                    url=vector.endpoint,
                    api_key=vector.credential.get_secret_value(),
                    timeout_seconds=5,
                    limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
                )
            if profile.object_store_id not in self._blobs:
                self._blobs[profile.object_store_id] = s3_client(blob)
        namespace = Namespace(org_id=current.org_id, kg_id=kg_id)
        generation = UUID(kg["generation"])
        return RoutedStores(
            namespace=namespace,
            authority=NamespaceRepository(self.policy.database.client[mongo.database], namespace),
            graph=GraphBinding(namespace, self._graphs[profile.neo4j_id]),
            vectors=VectorBinding(
                namespace,
                self._vectors[profile.qdrant_id],
                f"kg_{current.org_id.hex}_{kg_id.hex}_{generation.hex}",
            ),
            blobs=BlobBinding(namespace, self._blobs[profile.object_store_id], blob.bucket),
        )

    async def for_kg(self, access: AccessContext, kg_id: UUID) -> RoutedStores:
        stores = await self._bind(access, kg_id)
        try:
            await stores.graph.driver.verify_connectivity()
            await stores.vectors.client.get_collection(stores.vectors.collection)
        except Exception as exc:
            raise DomainError(
                ErrorCode.BACKEND_UNAVAILABLE,
                "A required KG backend is unavailable.",
                retryable=True,
            ) from exc
        await self.policy.revalidate(access)
        return stores

    async def provision(self, access: AccessContext, kg_id: UUID) -> None:
        stores = await self._bind(access, kg_id)
        try:
            await install_mongo_schema(stores.authority._database)
            await stores.graph.driver.execute_query(
                "CREATE CONSTRAINT scoped_projection_identity IF NOT EXISTS "
                "FOR (n:ScopedProjection) "
                "REQUIRE (n.org_id,n.kg_id,n.projection_id) IS UNIQUE"
            )
            if not await stores.vectors.client.collection_exists(stores.vectors.collection):
                try:
                    await stores.vectors.client.create_collection(
                        stores.vectors.collection,
                        vectors_config=models.VectorParams(
                            size=self.dimensions, distance=models.Distance.COSINE
                        ),
                    )
                except UnexpectedResponse:
                    if not await stores.vectors.client.collection_exists(stores.vectors.collection):
                        raise
            for field in (
                "org_id",
                "kg_id",
                "material_id",
                "source_version_id",
                "curation_revision_id",
                "generation",
                "attempt_id",
                "manifest_id",
            ):
                await stores.vectors.client.create_payload_index(
                    stores.vectors.collection, field, models.PayloadSchemaType.KEYWORD, wait=True
                )
            result = await stores.vectors.client.get_collection(stores.vectors.collection)
            config = result.config.params.vectors
            if not isinstance(config, models.VectorParams) or config.size != self.dimensions:
                raise ValueError("embedding dimension mismatch")
            buckets = await asyncio.to_thread(stores.blobs.client.list_buckets)
            if stores.blobs.bucket not in [bucket["Name"] for bucket in buckets.get("Buckets", [])]:
                await asyncio.to_thread(
                    stores.blobs.client.create_bucket, Bucket=stores.blobs.bucket
                )
        except Exception as exc:
            raise DomainError(
                ErrorCode.BACKEND_UNAVAILABLE,
                "KG namespace provisioning did not complete.",
                retryable=True,
            ) from exc
        await self.policy.revalidate(access)

    async def close(self) -> None:
        for graph_client in self._graphs.values():
            await graph_client.close()
        for vector_client in self._vectors.values():
            await vector_client.close()
        for blob_client in self._blobs.values():
            await asyncio.to_thread(blob_client.close)
        self._graphs.clear()
        self._vectors.clear()
        self._blobs.clear()
