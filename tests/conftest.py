"""Tiny real-store fixtures. No shared application state or corpus data."""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from neo4j import AsyncDriver, AsyncGraphDatabase
from pymongo import AsyncMongoClient, MongoClient
from pymongo.asynchronous.database import AsyncDatabase
from qdrant_client import AsyncQdrantClient

from domain_kg.settings import Settings
from domain_kg.storage.qdrant import open_qdrant


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


@pytest.fixture(scope="session")
def mongo_database_name():
    # Reuse only schema/index definitions. All synthetic documents are cleared
    # between cases; each run and each parallel pytest worker gets its own DB.
    name = f"domain_kg_smoke_{uuid4().hex}"
    yield name
    client = MongoClient(
        Settings().mongo_uri.get_secret_value(), serverSelectionTimeoutMS=5000, maxPoolSize=2
    )
    try:
        client.drop_database(name)
    finally:
        client.close()


@pytest.fixture
async def mongo_db(settings: Settings, mongo_database_name) -> AsyncIterator[AsyncDatabase]:
    client = AsyncMongoClient(
        settings.mongo_uri.get_secret_value(), serverSelectionTimeoutMS=5000, maxPoolSize=4
    )
    database = client[mongo_database_name]
    try:
        await client.admin.command("ping")
        yield database
    finally:
        for collection in await database.list_collection_names():
            if not collection.startswith("system."):
                await database[collection].delete_many({})
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
    client = await open_qdrant(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key.get_secret_value(),
        timeout_seconds=5,
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
        # Only this fixture's randomly allocated organization/profile can be
        # removed. Repeated smoke runs must not accumulate per-KG collections.
        owned = await mongo_db.kgs.find(
            {
                "org_id": str(org),
                "storage_profile_id": str(profile.id),
            }
        ).to_list()
        vector_client = router._vectors.get(profile.qdrant_id)
        for kg in owned:
            if vector_client is not None:
                from uuid import UUID

                collection = f"kg_{org.hex}_{UUID(kg['id']).hex}_{UUID(kg['generation']).hex}"
                if await vector_client.collection_exists(collection):
                    await vector_client.delete_collection(collection)
        graph_driver = router._graphs.get(profile.neo4j_id)
        if graph_driver is not None and owned:
            await graph_driver.execute_query(
                "MATCH (n {org_id:$org}) WHERE n.kg_id IN $kgs DETACH DELETE n",
                org=str(org),
                kgs=[kg["id"] for kg in owned],
            )
        for connection in approved.values():
            if connection.kind == "s3":
                from domain_kg.catalog.backends import s3_client

                client = s3_client(connection)
                try:
                    names = [item["Name"] for item in client.list_buckets().get("Buckets", [])]
                    if connection.bucket in names:
                        for page in client.get_paginator("list_objects_v2").paginate(
                            Bucket=connection.bucket
                        ):
                            objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
                            if objects:
                                client.delete_objects(
                                    Bucket=connection.bucket, Delete={"Objects": objects}
                                )
                        client.delete_bucket(Bucket=connection.bucket)
                finally:
                    client.close()
        await router.close()


@pytest.fixture
async def material_service(catalog, team):
    from domain_kg.catalog.models import CreateKG
    from domain_kg.identity.models import Action
    from domain_kg.materials.service import MaterialService

    catalog_service, _, router, profile = catalog
    _, policy, principals, members, org, _ = team
    creator = await policy.resolve_access(principals["owner"], [], Action.CREATE_KG)
    kg = await catalog_service.create_kg(
        creator,
        CreateKG(
            name="Materials",
            domain_profile_id=catalog_service.general_profile_id,
            placement_id=profile.id,
            idempotency_key="materials-kg",
        ),
    )
    for name, role in (("editor", "editor"), ("reader", "reader")):
        access = await policy.resolve_access(principals["owner"], [kg.kg_id], Action.MANAGE_GRANTS)
        await policy.set_grant(
            access, kg.kg_id, "member", members[name].id, role, idempotency_key=f"material-{name}"
        )
    service = MaterialService(policy, router)
    await service.initialize()
    return service, kg.kg_id


@pytest.fixture
async def blob_store(catalog, team):
    from domain_kg.catalog.models import CreateKG
    from domain_kg.identity.models import Action
    from domain_kg.storage.blobs import BlobStore

    service, _, router, profile = catalog
    _, policy, principals, _, org, _ = team
    kg = await service.create_kg(
        await policy.resolve_access(principals["owner"], [], Action.CREATE_KG),
        CreateKG(
            name="Blobs",
            domain_profile_id=service.general_profile_id,
            placement_id=profile.id,
            idempotency_key="blob-kg",
        ),
    )
    stores = await router.for_kg(
        await policy.resolve_access(principals["owner"], [kg.kg_id], Action.READ), kg.kg_id
    )
    return BlobStore(stores.blobs), kg.kg_id, org
