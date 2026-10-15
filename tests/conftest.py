"""Tiny real-store fixtures. No shared application state or corpus data."""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from neo4j import AsyncDriver, AsyncGraphDatabase
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from qdrant_client import AsyncQdrantClient

from domain_kg.settings import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
async def mongo_db(settings: Settings) -> AsyncIterator[AsyncDatabase]:
    client = AsyncMongoClient(settings.mongo_uri.get_secret_value(), serverSelectionTimeoutMS=5000)
    name = f"domain_kg_smoke_{uuid4().hex}"
    try:
        await client.admin.command("ping")
        yield client[name]
    finally:
        await client.drop_database(name)
        await client.close()


@pytest.fixture
async def graph(settings: Settings) -> AsyncIterator[AsyncDriver]:
    async with AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
        connection_timeout=5,
    ) as driver:
        await driver.verify_connectivity()
        yield driver


@pytest.fixture
async def vectors(settings: Settings) -> AsyncIterator[AsyncQdrantClient]:
    client = AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key.get_secret_value(),
        timeout=5,
    )
    try:
        yield client
    finally:
        await client.close()
