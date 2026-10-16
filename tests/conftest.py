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


@pytest.fixture
async def team(mongo_db):
    from domain_kg.identity.models import Principal
    from domain_kg.identity.policy import Policy
    from domain_kg.identity.repository import IdentityRepository

    identities = IdentityRepository(mongo_db)
    await identities.initialize()
    principals = {
        name: Principal(
            issuer="https://idp.example",
            subject=name,
            scopes={"domain_kg:read", "domain_kg:write", "domain_kg:admin"},
        )
        for name in ("owner", "editor", "reader", "denied")
    }
    org, owner = await identities.bootstrap_organization(principals["owner"], "Team")
    members = {"owner": owner}
    for name in ("editor", "reader", "denied"):
        members[name] = await identities.admit(
            owner, principals[name], role="member", idempotency_key=name
        )
    kg = uuid4()
    await mongo_db.kgs.insert_one(
        {"id": str(kg), "org_id": str(org), "state": "ready", "visibility_revision": 0}
    )
    for name in ("owner", "editor", "reader"):
        await mongo_db.kg_grants.insert_one(
            {
                "org_id": str(org),
                "kg_id": str(kg),
                "subject_kind": "member",
                "subject_id": str(members[name].id),
                "role": name,
            }
        )
    policy = Policy(mongo_db)
    await policy.initialize()
    return identities, policy, principals, members, org, kg
