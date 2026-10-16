"""The independent physical profile drill is invoked explicitly and runs serially."""

from uuid import uuid4

import pytest

pytestmark = pytest.mark.storage


@pytest.mark.physical
async def test_independent_physical_profiles_serially_under_memory_cap(
    catalog, team, mongo_db, settings
) -> None:
    import asyncio
    import json
    import subprocess

    from domain_kg.catalog.backends import BackendRegistry, PrivateConnection
    from domain_kg.catalog.models import CreateKG, RegisterBackend, StorageProfile
    from domain_kg.catalog.service import CatalogService
    from domain_kg.errors import DomainError
    from domain_kg.identity.models import Action
    from domain_kg.models import ScopedRef
    from domain_kg.storage.integrity import (
        MaterialRecord,
        MaterialVersionRecord,
        ProjectionSchema,
        ProjectionStore,
    )
    from domain_kg.storage.router import KGStoreRouter

    service, registry, primary_router, profile = catalog
    _, policy, principals, _, org, _ = team
    owner = principals["owner"]

    async def access(kg=None, action=Action.READ):
        return await policy.resolve_access(owner, [kg] if kg else [], action)

    first = await service.create_kg(
        await access(action=Action.CREATE_KG),
        CreateKG(
            name="Physical A",
            domain_profile_id=service.general_profile_id,
            placement_id=profile.id,
            idempotency_key="physical-a",
        ),
    )
    stores_a = await primary_router.for_kg(await access(first.kg_id), first.kg_id)
    shared = await service.create_kg(
        await access(action=Action.CREATE_KG),
        CreateKG(
            name="Shared A",
            domain_profile_id=service.general_profile_id,
            placement_id=profile.id,
            idempotency_key="shared-a",
        ),
    )
    stores_shared = await primary_router.for_kg(await access(shared.kg_id), shared.kg_id)
    assert stores_shared.graph.driver is stores_a.graph.driver
    assert stores_shared.vectors.client is stores_a.vectors.client
    assert stores_shared.vectors.collection != stores_a.vectors.collection

    async def write(stores, label):
        namespace = stores.namespace.model_dump()
        material, version = uuid4(), uuid4()
        await stores.authority.insert(MaterialRecord(**namespace, id=material, title=label))
        await stores.authority.insert(
            MaterialVersionRecord(
                **namespace, id=version, material=ScopedRef(**namespace, id=material)
            )
        )
        projection = ProjectionStore(
            stores.authority, stores.graph.driver, stores.vectors.client, stores.vectors.collection
        )
        await projection.initialize(dimensions=3)
        await projection.stage(
            ProjectionSchema(
                **namespace,
                id=uuid4(),
                source=ScopedRef(**namespace, id=version),
                generation=uuid4(),
                attempt_id=uuid4(),
                vector=[1.0, 0.0, 0.0],
            )
        )

    await write(stores_a, "Primary evidence")
    await write(stores_shared, "Independently namespaced shared evidence")
    assert (await stores_a.vectors.client.count(stores_a.vectors.collection, exact=True)).count == 1
    await primary_router.close()

    async def compose(arguments, *, secondary=False):
        args = ["docker", "compose"]
        if secondary:
            args += [
                "-f",
                "compose.yaml",
                "-f",
                "compose.multi-backend.yaml",
                "--profile",
                "multi-backend",
            ]
        return await asyncio.to_thread(
            subprocess.run,
            [*args, *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )

    async def memory():
        result = await compose(["ps", "--quiet"])
        inspect = await asyncio.to_thread(
            subprocess.check_output, ["docker", "inspect", *result.stdout.split()], text=True
        )
        containers = json.loads(inspect)
        assert len(containers) == 4
        assert sum(row["HostConfig"]["Memory"] for row in containers) <= 1_500_000_000
        assert all(
            row["HostConfig"]["Memory"] == row["HostConfig"]["MemorySwap"] for row in containers
        )

    router_b = None
    router_restored = None
    secondary_db = f"{mongo_db.name}_secondary"
    try:
        await compose(["stop", "--timeout", "20", "neo4j", "qdrant"])
        await compose(
            ["up", "-d", "--wait", "--wait-timeout", "120", "neo4j", "qdrant"], secondary=True
        )
        await memory()
        approved = dict(registry._approved)
        secondary = [
            PrivateConnection(kind="mongo", database=secondary_db),
            PrivateConnection(
                kind="neo4j",
                endpoint="bolt://127.0.0.1:28687",
                username=settings.neo4j_user,
                credential=settings.neo4j_password,
            ),
            PrivateConnection(
                kind="qdrant", endpoint="http://127.0.0.1:27333", credential=settings.qdrant_api_key
            ),
        ]
        registry_b = BackendRegistry(
            policy, approved | {uuid4(): connection for connection in secondary}
        )
        identifiers = {}
        for reference, connection in registry_b._approved.items():
            if connection in secondary:
                result = await registry_b.register(
                    await access(action=Action.REGISTER_BACKEND),
                    RegisterBackend(kind=connection.kind, secret_ref=reference),
                    idempotency_key=str(reference),
                )
                identifiers[connection.kind] = result.backend_id
        profile_b = StorageProfile(
            id=uuid4(),
            revision=0,
            mongo_id=identifiers["mongo"],
            neo4j_id=identifiers["neo4j"],
            qdrant_id=identifiers["qdrant"],
            object_store_id=profile.object_store_id,
            allowed_org_ids={org},
        )
        await registry_b.register_profile(
            await access(action=Action.REGISTER_BACKEND),
            profile_b,
            idempotency_key="physical-b-profile",
        )
        router_b = KGStoreRouter(policy, registry_b, dimensions=3)
        service_b = CatalogService(policy, registry_b, router_b)
        second = await service_b.create_kg(
            await access(action=Action.CREATE_KG),
            CreateKG(
                name="Physical B",
                domain_profile_id=service.general_profile_id,
                placement_id=profile_b.id,
                idempotency_key="physical-b",
            ),
        )
        stores_b = await router_b.for_kg(await access(second.kg_id), second.kg_id)
        assert stores_b.authority._database.name != stores_a.authority._database.name
        assert (
            await stores_b.vectors.client.count(stores_b.vectors.collection, exact=True)
        ).count == 0
        await write(stores_b, "Independent evidence")
        assert (
            await stores_b.vectors.client.count(stores_b.vectors.collection, exact=True)
        ).count == 1
        with pytest.raises(DomainError):
            await router_b.for_kg(await access(first.kg_id), first.kg_id)
        await stores_b.vectors.client.delete_collection(stores_b.vectors.collection)
        await stores_b.graph.driver.execute_query(
            "MATCH (n:ScopedProjection {org_id:$org,kg_id:$kg}) DETACH DELETE n",
            org=str(org),
            kg=str(second.kg_id),
        )
        await router_b.close()
        router_b = None
    finally:
        if router_b:
            await router_b.close()
        await compose(["stop", "--timeout", "20", "neo4j", "qdrant"], secondary=True)
        await compose(["up", "-d", "--wait", "--wait-timeout", "120", "neo4j", "qdrant"])
        await memory()
        router_restored = KGStoreRouter(policy, registry, dimensions=3)
        try:
            for primary in (first, shared):
                restored = await router_restored.for_kg(await access(primary.kg_id), primary.kg_id)
                assert (
                    await restored.vectors.client.count(restored.vectors.collection, exact=True)
                ).count == 1
                await restored.vectors.client.delete_collection(restored.vectors.collection)
                await restored.graph.driver.execute_query(
                    "MATCH (n:ScopedProjection {org_id:$org,kg_id:$kg}) DETACH DELETE n",
                    org=str(org),
                    kg=str(primary.kg_id),
                )
        finally:
            await router_restored.close()
            await mongo_db.client.drop_database(secondary_db)


async def test_profile_allowance_cannot_be_claimed_by_another_organization(team, mongo_db) -> None:
    from domain_kg.catalog.backends import BackendRegistry
    from domain_kg.catalog.models import CreateKG, StorageProfile
    from domain_kg.catalog.service import CatalogService
    from domain_kg.errors import DomainError
    from domain_kg.identity.models import Action
    from domain_kg.storage.router import KGStoreRouter

    _, policy, principals, _, _, _ = team
    profile = StorageProfile(
        id=uuid4(),
        revision=0,
        mongo_id=uuid4(),
        neo4j_id=uuid4(),
        qdrant_id=uuid4(),
        object_store_id=uuid4(),
        allowed_org_ids={uuid4()},
    )
    await mongo_db.storage_profiles.insert_one(
        profile.model_dump(mode="json") | {"_id": str(profile.id)}
    )
    registry = BackendRegistry(policy, {})
    router = KGStoreRouter(policy, registry, dimensions=3)
    service = CatalogService(policy, registry, router)
    await service.initialize()
    access = await policy.resolve_access(principals["owner"], [], Action.CREATE_KG)
    with pytest.raises(DomainError):
        await service.create_kg(
            access,
            CreateKG(
                name="Forbidden",
                domain_profile_id=service.general_profile_id,
                placement_id=profile.id,
                idempotency_key="wrong-org",
            ),
        )
