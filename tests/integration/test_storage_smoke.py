"""Real transactions, source-linked facts, filters, and S3 byte round-trips."""

import asyncio
from uuid import uuid4

import boto3
import pytest
from botocore.config import Config
from neo4j import AsyncDriver
from pymongo.asynchronous.database import AsyncDatabase
from qdrant_client import AsyncQdrantClient, models

from domain_kg.settings import Settings

pytestmark = pytest.mark.storage


async def test_mongo_replica_set_commit_and_abort(mongo_db: AsyncDatabase) -> None:
    async with mongo_db.client.start_session() as session:
        async with await session.start_transaction():
            await mongo_db.materials.insert_one({"_id": "accepted"}, session=session)
        assert await mongo_db.materials.find_one({"_id": "accepted"}) is not None
        with pytest.raises(RuntimeError, match="abort fixture"):
            async with await session.start_transaction():
                await mongo_db.materials.insert_one({"_id": "aborted"}, session=session)
                raise RuntimeError("abort fixture")
        assert await mongo_db.materials.find_one({"_id": "aborted"}) is None


async def test_neo4j_source_link_and_fulltext(graph: AsyncDriver) -> None:
    org = uuid4().hex
    index = "domain_kg_smoke_text"
    try:
        await graph.execute_query(
            f"CREATE FULLTEXT INDEX {index} IF NOT EXISTS FOR (o:SmokeObservation) ON EACH [o.text]"
        )
        await graph.execute_query(
            "CREATE (o:SmokeObservation {org:$org, kg:'kg-a', text:'storage evaluation'})"
            "-[:SUPPORTED_BY]->(:SmokeSource {org:$org, kg:'kg-a', version:'version-a'})",
            org=org,
        )
        await graph.execute_query("CALL db.awaitIndexes(30)")
        records, _, _ = await graph.execute_query(
            "CALL db.index.fulltext.queryNodes($index, $query) YIELD node "
            "MATCH (node)-[:SUPPORTED_BY]->(s:SmokeSource) "
            "WHERE node.org=$org AND s.org=$org AND s.kg='kg-a' "
            "RETURN s.version AS source_version",
            index=index,
            query="storage",
            org=org,
        )
        assert [r["source_version"] for r in records] == ["version-a"]
    finally:
        await graph.execute_query("MATCH (n {org:$org}) DETACH DELETE n", org=org)


async def test_qdrant_payload_filter_before_top_k(vectors: AsyncQdrantClient) -> None:
    collection = f"smoke_{uuid4().hex}"
    try:
        await vectors.create_collection(
            collection, vectors_config=models.VectorParams(size=3, distance=models.Distance.COSINE)
        )
        await vectors.upsert(
            collection,
            wait=True,
            points=[
                models.PointStruct(id=1, vector=[1.0, 0.0, 0.0], payload={"kg": "hidden"}),
                models.PointStruct(id=2, vector=[0.9, 0.1, 0.0], payload={"kg": "visible"}),
            ],
        )
        result = await vectors.query_points(
            collection,
            query=[1.0, 0.0, 0.0],
            limit=1,
            query_filter=models.Filter(
                must=[models.FieldCondition(key="kg", match=models.MatchValue(value="visible"))]
            ),
        )
        assert [point.id for point in result.points] == [2]
    finally:
        await vectors.delete_collection(collection)


async def test_object_store_preserves_original_bytes(settings: Settings) -> None:
    # boto3 is synchronous; exercise it in a worker thread, never on the event loop.
    def round_trip() -> None:
        bucket = f"smoke-{uuid4().hex}"
        key = f"org/{uuid4()}/kg/{uuid4()}/original.txt"
        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
            region_name="us-east-1",
            # This laptop probe addresses a loopback service directly. A system
            # HTTP proxy can alter Expect/Continue uploads and their checksums.
            config=Config(
                connect_timeout=5, read_timeout=5, retries={"max_attempts": 1}, proxies={}
            ),
        )
        client.create_bucket(Bucket=bucket)
        try:
            client.put_object(Bucket=bucket, Key=key, Body=b"source evidence\n")
            result = client.get_object(Bucket=bucket, Key=key)
            with result["Body"] as body:
                assert body.read() == b"source evidence\n"
        finally:
            client.delete_object(Bucket=bucket, Key=key)
            client.delete_bucket(Bucket=bucket)
            client.close()

    await asyncio.to_thread(round_trip)
