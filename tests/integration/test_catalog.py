"""Approved placement, idempotent KG creation and explicit owner membership."""

from uuid import uuid4

import pytest

from domain_kg.errors import DomainError
from domain_kg.identity.models import Action

pytestmark = pytest.mark.storage


async def test_deleted_kg_restores_its_previous_read_only_state(material_service, catalog, team):
    _, kg = material_service
    service, policy, principal = catalog[0], team[1], team[2]["owner"]

    async def owner():
        return await policy.resolve_access(principal, [kg], Action.CONFIGURE)

    revision = (await service.get_kg(await owner(), kg))["revision"]
    frozen = await service.change_state(
        await owner(), kg, "read_only", expected_revision=revision, idempotency_key="freeze-delete"
    )
    deleted = await service.delete_kg(
        await owner(), kg, expected_revision=frozen.revision, idempotency_key="delete-frozen"
    )
    restored = await service.restore_kg(
        await owner(), kg, expected_revision=deleted.revision, idempotency_key="restore-frozen"
    )
    assert restored.state == "read_only"
    with pytest.raises(DomainError):
        await policy.resolve_access(principal, [kg], Action.INGEST)


async def test_deleted_kg_fences_reads_preserves_sources_and_requires_explicit_owner_restore(
    material_service, catalog, team
):
    from domain_kg.catalog.routes import KGStateChange
    from domain_kg.materials.models import AddMaterial
    from domain_kg.models import PageRequest

    materials, kg = material_service
    service = catalog[0]
    _, policy, principals, _, org, _ = team

    async def owner():
        return await policy.resolve_access(principals["owner"], [kg], Action.CONFIGURE)

    added = await materials.add(
        await policy.resolve_access(principals["editor"], [kg], Action.INGEST),
        AddMaterial(kg_id=kg, title="Retained source", inline_text="Alpha", idempotency_key="keep"),
    )
    before = await service.get_kg(await owner(), kg)
    deleted = await service.delete_kg(
        await owner(), kg, expected_revision=before["revision"], idempotency_key="delete-kg"
    )
    assert deleted.state == "deleted"
    replay = await service.delete_kg(
        await owner(), kg, expected_revision=before["revision"], idempotency_key="delete-kg"
    )
    assert replay == deleted
    for name in ("owner", "editor", "reader"):
        with pytest.raises(DomainError):
            await policy.resolve_access(principals[name], [kg], Action.READ)
    listing = await service.list_kgs(
        await policy.resolve_access(principals["owner"], [], Action.LIST_KGS), PageRequest()
    )
    assert str(kg) not in str(listing)
    trash = await service.list_kgs(
        await policy.resolve_access(principals["owner"], [], Action.LIST_KGS),
        PageRequest(),
        lifecycle="deleted",
    )
    assert str(kg) in str(trash)
    with pytest.raises(DomainError):
        await service.change_state(
            await owner(),
            kg,
            "ready",
            expected_revision=deleted.revision,
            idempotency_key="bypass-restore",
        )
    with pytest.raises(DomainError):
        await service.restore_kg(
            await policy.resolve_access(principals["editor"], [kg], Action.REPLACE),
            kg,
            expected_revision=deleted.revision,
            idempotency_key="editor-restore",
        )
    restored = await service.restore_kg(
        await owner(), kg, expected_revision=deleted.revision, idempotency_key="restore-kg"
    )
    assert restored.state == "ready"
    assert (
        await materials.database.materials.count_documents(
            {"org_id": str(org), "kg_id": str(kg), "id": str(added.material_id)}
        )
        == 1
    )
    renamed = KGStateChange(
        name="Renamed KG", expected_revision=restored.revision, idempotency_key="rename"
    )
    await service.change_state(
        await owner(),
        kg,
        renamed.state,
        name=renamed.name,
        expected_revision=renamed.expected_revision,
        idempotency_key=renamed.idempotency_key,
    )
    assert (await service.get_kg(await owner(), kg))["name"] == "Renamed KG"
    with pytest.raises(DomainError) as stale:
        await service.delete_kg(
            await owner(), kg, expected_revision=restored.revision, idempotency_key="stale-delete"
        )
    assert stale.value.code == "VERSION_CONFLICT"


async def test_operator_invitation_does_not_grant_kg_access(team, mongo_db) -> None:
    from domain_kg.catalog.backends import invite_operator

    _, policy, principals, members, org, kg = team
    await invite_operator(mongo_db, principals["denied"].issuer, principals["denied"].subject)
    assert (
        await policy.resolve_access(principals["denied"], [], Action.REGISTER_BACKEND)
    ).platform_operator
    with pytest.raises(DomainError):
        await policy.resolve_access(principals["denied"], [kg], Action.READ)
    with pytest.raises(DomainError):
        await invite_operator(mongo_db, "https://unapproved.example", "unknown")


async def test_catalog_list_never_reveals_ungranted_names(catalog, team) -> None:
    from domain_kg.catalog.models import CreateKG
    from domain_kg.models import PageRequest

    service, _, _, profile = catalog
    _, policy, principals, members, org, _ = team
    creator = await policy.resolve_access(principals["owner"], [], Action.CREATE_KG)
    first = await service.create_kg(
        creator,
        CreateKG(
            name="Hidden name",
            domain_profile_id=service.general_profile_id,
            placement_id=profile.id,
            idempotency_key="list-hidden",
        ),
    )
    reader = await policy.resolve_access(principals["reader"], [], Action.LIST_KGS, org_id=org)
    page = await service.list_kgs(reader, PageRequest())
    assert "Hidden name" not in str(page)
    owner = await policy.resolve_access(principals["owner"], [first.kg_id], Action.MANAGE_GRANTS)
    await policy.set_grant(
        owner, first.kg_id, "member", members["reader"].id, "reader", idempotency_key="show-name"
    )
    reader = await policy.resolve_access(principals["reader"], [], Action.LIST_KGS, org_id=org)
    assert "Hidden name" in str(await service.list_kgs(reader, PageRequest()))


async def test_profile_detach_racing_creation_retains_reference(
    catalog, team, mongo_db, monkeypatch
) -> None:
    from pymongo.asynchronous.collection import AsyncCollection

    from domain_kg.catalog.models import CreateKG

    service, registry, _, profile = catalog
    _, policy, principals, _, _, _ = team
    unused = profile.model_copy(update={"id": uuid4()})
    operator = await policy.resolve_access(principals["owner"], [], Action.REGISTER_BACKEND)
    await registry.register_profile(operator, unused, idempotency_key="unused-profile")
    original = AsyncCollection.find_one
    raced = False

    async def interleaved(collection, *args, **kwargs):
        nonlocal raced
        result = await original(collection, *args, **kwargs)
        query = args[0] if args else {}
        if (
            collection.name == "kgs"
            and query.get("storage_profile_id") == str(unused.id)
            and not raced
        ):
            raced = True
            access = await policy.resolve_access(principals["owner"], [], Action.CREATE_KG)
            await service.create_kg(
                access,
                CreateKG(
                    name="Racing KG",
                    domain_profile_id=service.general_profile_id,
                    placement_id=unused.id,
                    idempotency_key="racing-kg",
                ),
            )
        return result

    monkeypatch.setattr(AsyncCollection, "find_one", interleaved)
    with pytest.raises(DomainError):
        await registry.detach_profile(operator, unused.id)
    assert await mongo_db.storage_profiles.find_one({"id": str(unused.id)}) is not None


async def test_concurrent_idempotent_kg_creation(catalog, team, mongo_db) -> None:
    import asyncio

    from domain_kg.catalog.models import CreateKG

    service, _, _, profile = catalog
    _, policy, principals, _, org, _ = team
    access = await policy.resolve_access(principals["owner"], [], Action.CREATE_KG)
    command = CreateKG(
        name="One KG",
        domain_profile_id=service.general_profile_id,
        placement_id=profile.id,
        idempotency_key="one-kg",
    )
    results = await asyncio.gather(
        service.create_kg(access, command), service.create_kg(access, command)
    )
    assert results[0].kg_id == results[1].kg_id
    assert await mongo_db.kgs.count_documents({"org_id": str(org), "name": "One KG"}) == 1


async def test_catalog_creation_routing_and_lifecycle(catalog, team, mongo_db, settings) -> None:
    from domain_kg.catalog.models import CreateKG
    from domain_kg.storage.integrity import MaterialRecord

    service, registry, router, profile = catalog
    _, policy, principals, members, org, _ = team

    async def create(key):
        access = await policy.resolve_access(principals["owner"], [], Action.CREATE_KG)
        return await service.create_kg(
            access,
            CreateKG(
                name=key,
                domain_profile_id=service.general_profile_id,
                placement_id=profile.id,
                idempotency_key=key,
            ),
        )

    first, second = await create("first"), await create("second")
    assert first.state == second.state == "ready"
    assert (await create("first")).kg_id == first.kg_id
    assert first.kg_id != second.kg_id
    stores = [
        await router.for_kg(await policy.resolve_access(principals["owner"], [kg], Action.READ), kg)
        for kg in (first.kg_id, second.kg_id)
    ]
    material = uuid4()
    await stores[0].authority.insert(
        MaterialRecord(org_id=org, kg_id=first.kg_id, id=material, title="Only first")
    )
    assert stores[0].graph.driver is stores[1].graph.driver
    assert stores[0].vectors.collection != stores[1].vectors.collection
    assert (
        await mongo_db.materials.count_documents(
            {"org_id": str(org), "kg_id": str(second.kg_id), "id": str(material)}
        )
        == 0
    )
    owner = await policy.resolve_access(principals["owner"], [first.kg_id], Action.MANAGE_GRANTS)
    await policy.set_grant(
        owner, first.kg_id, "member", members["reader"].id, "reader", idempotency_key="reader"
    )
    reader = await policy.resolve_access(principals["reader"], [first.kg_id], Action.READ)
    summary = await service.get_kg(reader, first.kg_id)
    assert "storage_profile_id" not in summary
    health = await service.health(reader, first.kg_id)
    assert settings.neo4j_password.get_secret_value() not in str(health)
    assert settings.neo4j_uri not in str(health)
    owner = await policy.resolve_access(principals["owner"], [first.kg_id], Action.CONFIGURE)
    receipt = await service.change_state(
        owner,
        first.kg_id,
        "read_only",
        expected_revision=first.revision,
        idempotency_key="read-only",
    )
    assert receipt.state == "read_only"
    with pytest.raises(DomainError):
        await policy.resolve_access(principals["owner"], [first.kg_id], Action.INGEST)
    owner = await policy.resolve_access(principals["owner"], [first.kg_id], Action.CONFIGURE)
    await service.change_state(
        owner,
        first.kg_id,
        "archived",
        expected_revision=receipt.revision,
        idempotency_key="archive",
    )
    with pytest.raises(DomainError):
        await policy.resolve_access(principals["reader"], [first.kg_id], Action.READ)
    operator = await policy.resolve_access(principals["owner"], [], Action.REGISTER_BACKEND)
    with pytest.raises(DomainError):
        await registry.detach_backend(operator, profile.neo4j_id)
    with pytest.raises(DomainError):
        await registry.detach_profile(operator, profile.id)


async def test_backend_registration_is_operator_only_and_preapproved(catalog, team) -> None:
    from pydantic import ValidationError

    from domain_kg.catalog.models import RegisterBackend

    _, registry, _, profile = catalog
    _, policy, principals, members, org, kg = team
    editor = await policy.resolve_access(principals["editor"], [kg], Action.READ)
    with pytest.raises(DomainError):
        await registry.register(
            editor, RegisterBackend(kind="neo4j", secret_ref=uuid4()), idempotency_key="unapproved"
        )
    operator = await policy.resolve_access(principals["owner"], [], Action.REGISTER_BACKEND)
    with pytest.raises(DomainError):
        await registry.register(
            operator,
            RegisterBackend(kind="neo4j", secret_ref=uuid4()),
            idempotency_key="unknown-ref",
        )
    with pytest.raises(ValidationError):
        RegisterBackend.model_validate(
            {"kind": "neo4j", "secret_ref": uuid4(), "dsn": "bolt://untrusted.example"}
        )


async def test_approved_qdrant_connection_ignores_ambient_proxy(catalog, team, monkeypatch):
    from domain_kg.identity.models import Action

    _, registry, _, _ = catalog
    _, policy, principals, _, _, _ = team
    access = await policy.resolve_access(principals["owner"], [], Action.REGISTER_BACKEND)
    record = next(item for item in await registry.list_backends(access) if item.kind == "qdrant")
    connection = await registry.connection(record.backend_id, "qdrant")
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "")
    assert (await registry.probe(record.backend_id, connection)).healthy


async def test_backend_compatibility_probe_uses_the_approved_transport(catalog, team, monkeypatch):
    import asyncio
    import threading

    import httpx

    _, registry, _, _ = catalog
    _, policy, principals, _, _, _ = team
    access = await policy.resolve_access(principals["owner"], [], Action.REGISTER_BACKEND)
    target = next(item for item in await registry.list_backends(access) if item.kind == "qdrant")
    ambient = threading.Event()

    def unsafe_default_transport(*args, **kwargs):
        ambient.set()
        return httpx.Response(200, json={"version": "1.19.2"})

    monkeypatch.setattr(httpx, "get", unsafe_default_transport)
    assert (
        await registry.probe(
            target.backend_id, await registry.connection(target.backend_id, "qdrant")
        )
    ).healthy
    await asyncio.sleep(0.1)
    assert not ambient.is_set(), "SDK background probe bypassed the configured private transport"


async def test_direct_compatibility_check_rejects_incompatible_server(catalog, team, monkeypatch):
    from types import SimpleNamespace

    from qdrant_client import AsyncQdrantClient

    _, registry, _, _ = catalog
    _, policy, principals, _, _, _ = team
    access = await policy.resolve_access(principals["owner"], [], Action.REGISTER_BACKEND)
    target = next(item for item in await registry.list_backends(access) if item.kind == "qdrant")

    async def incompatible(_client):
        return SimpleNamespace(version="99.0.0")

    monkeypatch.setattr(AsyncQdrantClient, "info", incompatible)
    with pytest.raises(DomainError) as rejected:
        await registry.probe(
            target.backend_id, await registry.connection(target.backend_id, "qdrant")
        )
    assert rejected.value.code == "BACKEND_UNAVAILABLE"
