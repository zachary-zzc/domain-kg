"""Tiny real-store fixtures. No shared application state or corpus data."""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from neo4j import AsyncDriver, AsyncGraphDatabase
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from qdrant_client import AsyncQdrantClient

from domain_kg.settings import Settings


def pytest_addoption(parser):
    parser.addoption(
        "--run-physical", action="store_true", help="run the serial independent-backend drill"
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-physical"):
        for item in items:
            if "physical" in item.keywords:
                item.add_marker(
                    pytest.mark.skip(
                        reason="requires --run-physical; swaps local graph/vector containers"
                    )
                )


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
        {
            "id": str(kg),
            "org_id": str(org),
            "state": "ready",
            "visibility_revision": 0,
            "name": "Existing KG",
            "revision": 0,
            "published_revision": 0,
        }
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


@pytest.fixture
async def catalog(team, mongo_db, settings):
    from domain_kg.catalog.backends import BackendRegistry, PrivateConnection
    from domain_kg.catalog.models import RegisterBackend, StorageProfile
    from domain_kg.catalog.service import CatalogService
    from domain_kg.identity.models import Action
    from domain_kg.storage.router import KGStoreRouter

    _, policy, principals, members, org, _ = team
    await mongo_db.platform_operators.insert_one(
        {
            "issuer": principals["owner"].issuer,
            "subject": principals["owner"].subject,
            "active": True,
        }
    )
    approved = {
        uuid4(): PrivateConnection(kind="mongo", database=mongo_db.name),
        uuid4(): PrivateConnection(
            kind="neo4j",
            endpoint=settings.neo4j_uri,
            username=settings.neo4j_user,
            credential=settings.neo4j_password,
        ),
        uuid4(): PrivateConnection(
            kind="qdrant", endpoint=settings.qdrant_url, credential=settings.qdrant_api_key
        ),
        uuid4(): PrivateConnection(
            kind="s3",
            endpoint=settings.s3_endpoint,
            username=settings.s3_access_key,
            credential=settings.s3_secret_key,
            bucket=f"catalog-{uuid4().hex}",
        ),
    }
    registry = BackendRegistry(policy, approved)
    await registry.initialize()
    backend_ids = {}
    for reference, connection in approved.items():
        access = await policy.resolve_access(principals["owner"], [], Action.REGISTER_BACKEND)
        backend = await registry.register(
            access,
            RegisterBackend(kind=connection.kind, secret_ref=reference),
            idempotency_key=str(reference),
        )
        backend_ids[connection.kind] = backend.backend_id
    profile = StorageProfile(
        id=uuid4(),
        revision=0,
        mongo_id=backend_ids["mongo"],
        neo4j_id=backend_ids["neo4j"],
        qdrant_id=backend_ids["qdrant"],
        object_store_id=backend_ids["s3"],
        allowed_org_ids={org},
    )
    access = await policy.resolve_access(principals["owner"], [], Action.REGISTER_BACKEND)
    await registry.register_profile(access, profile, idempotency_key="profile")
    router = KGStoreRouter(policy, registry, dimensions=3)
    service = CatalogService(policy, registry, router)
    await service.initialize()
    try:
        yield service, registry, router, profile
    finally:
        for connection in approved.values():
            if connection.kind == "s3":
                from domain_kg.catalog.backends import s3_client

                client = s3_client(connection)
                try:
                    names = [item["Name"] for item in client.list_buckets().get("Buckets", [])]
                    if connection.bucket in names:
                        client.delete_bucket(Bucket=connection.bucket)
                finally:
                    client.close()
        await router.close()
