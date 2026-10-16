"""Use real stores; MongoDB indexes are not cross-document foreign keys."""

from uuid import uuid4

import pytest
from pymongo.errors import DuplicateKeyError, WriteError

pytestmark = pytest.mark.storage


async def test_mongo_validators_indexes_and_scoped_reference_checks(mongo_db) -> None:
    from domain_kg.errors import DomainError
    from domain_kg.models import Namespace, ScopedRef
    from domain_kg.storage.integrity import (
        AssertionRecord,
        EntityRecord,
        MaterialRecord,
        MaterialVersionRecord,
        NamespaceRepository,
        install_mongo_schema,
    )

    await install_mongo_schema(mongo_db)
    org, kg_a, kg_b, identifier = uuid4(), uuid4(), uuid4(), uuid4()
    a, b = Namespace(org_id=org, kg_id=kg_a), Namespace(org_id=org, kg_id=kg_b)
    ra, rb = NamespaceRepository(mongo_db, a), NamespaceRepository(mongo_db, b)
    await ra.insert(MaterialRecord(**a.model_dump(), id=identifier, title="A"))
    await rb.insert(MaterialRecord(**b.model_dump(), id=identifier, title="B"))
    with pytest.raises(DuplicateKeyError):
        await ra.insert(MaterialRecord(**a.model_dump(), id=identifier, title="Duplicate"))
    with pytest.raises(WriteError):
        await mongo_db.materials.insert_one({"id": str(uuid4()), "title": "Missing namespace"})
    foreign = ScopedRef(**b.model_dump(), id=identifier)
    with pytest.raises(DomainError) as denied:
        await ra.insert(MaterialVersionRecord(**a.model_dump(), id=uuid4(), material=foreign))
    assert denied.value.code == "RESOURCE_NOT_ACCESSIBLE"
    assert await mongo_db.material_versions.count_documents({}) == 0

    entity_a, entity_b = uuid4(), uuid4()
    await ra.insert(EntityRecord(**a.model_dump(), id=entity_a))
    await rb.insert(EntityRecord(**b.model_dump(), id=entity_b))
    # A forged same-KG reference to an entity that exists only in B must fail too.
    for target in [
        ScopedRef(**b.model_dump(), id=entity_b),
        ScopedRef(**a.model_dump(), id=entity_b),
    ]:
        with pytest.raises(DomainError):
            await ra.insert(
                AssertionRecord(
                    **a.model_dump(),
                    id=uuid4(),
                    subject=ScopedRef(**a.model_dump(), id=entity_a),
                    target=target,
                    predicate="CITES",
                )
            )
    assert await mongo_db.assertions.count_documents({}) == 0


async def test_support_reference_validation_and_reconciliation(mongo_db) -> None:
    from domain_kg.errors import DomainError
    from domain_kg.models import Namespace, ScopedRef
    from domain_kg.storage.integrity import (
        AssertionRecord,
        EntityRecord,
        MaterialRecord,
        MaterialVersionRecord,
        NamespaceRepository,
        SupportRecord,
        install_mongo_schema,
    )

    await install_mongo_schema(mongo_db)
    a = Namespace(org_id=uuid4(), kg_id=uuid4())
    b = Namespace(org_id=a.org_id, kg_id=uuid4())
    ra, rb = NamespaceRepository(mongo_db, a), NamespaceRepository(mongo_db, b)
    first, second, assertion, material, version = [uuid4() for _ in range(5)]
    for identifier in [first, second]:
        await ra.insert(EntityRecord(**a.model_dump(), id=identifier))
    await ra.insert(
        AssertionRecord(
            **a.model_dump(),
            id=assertion,
            subject=ScopedRef(**a.model_dump(), id=first),
            target=ScopedRef(**a.model_dump(), id=second),
            predicate="CITES",
        )
    )
    await rb.insert(MaterialRecord(**b.model_dump(), id=material, title="Hidden"))
    await rb.insert(
        MaterialVersionRecord(
            **b.model_dump(), id=version, material=ScopedRef(**b.model_dump(), id=material)
        )
    )
    with pytest.raises(DomainError):
        await ra.insert(
            SupportRecord(
                **a.model_dump(),
                id=uuid4(),
                assertion=ScopedRef(**a.model_dump(), id=assertion),
                version=ScopedRef(**b.model_dump(), id=version),
                status="imported_unverified",
            )
        )
    assert await mongo_db.supports.count_documents({}) == 0
    # Simulate privileged out-of-band corruption. Reconciliation must find it.
    await mongo_db.assertions.update_one(
        {"org_id": str(a.org_id), "kg_id": str(a.kg_id), "id": str(assertion)},
        {"$set": {"target.id": str(uuid4())}},
    )
    issues = await ra.reconcile()
    assert len(issues) == 1 and issues[0].record_id == assertion


async def test_source_bound_projection_schemas_refuse_namespace_crossover(
    mongo_db,
    graph,
    vectors,
) -> None:
    from domain_kg.errors import DomainError
    from domain_kg.models import Namespace, ScopedRef
    from domain_kg.storage.integrity import (
        MaterialRecord,
        MaterialVersionRecord,
        NamespaceRepository,
        ProjectionSchema,
        ProjectionStore,
        install_mongo_schema,
    )

    await install_mongo_schema(mongo_db)
    scope = Namespace(org_id=uuid4(), kg_id=uuid4())
    repository = NamespaceRepository(mongo_db, scope)
    material, version = uuid4(), uuid4()
    await repository.insert(MaterialRecord(**scope.model_dump(), id=material, title="Source"))
    await repository.insert(
        MaterialVersionRecord(
            **scope.model_dump(), id=version, material=ScopedRef(**scope.model_dump(), id=material)
        )
    )
    collection = f"schema_{uuid4().hex}"
    store = ProjectionStore(repository, graph, vectors, collection)
    await store.initialize(dimensions=3)
    try:
        point = ProjectionSchema(
            **scope.model_dump(),
            id=uuid4(),
            source=ScopedRef(**scope.model_dump(), id=version),
            generation=uuid4(),
            attempt_id=uuid4(),
            vector=[1.0, 0.0, 0.0],
        )
        await store.stage(point)
        with pytest.raises(DomainError):
            await store.stage(
                point.model_copy(
                    update={
                        "id": uuid4(),
                        "source": point.source.model_copy(update={"kg_id": uuid4()}),
                    }
                )
            )
        assert (await vectors.count(collection, exact=True)).count == 1
        assert await store.reconcile() == []
        # A graph-only orphan must be detected, not just missing graph counterparts.
        from qdrant_client.models import PointIdsList

        await vectors.delete(collection, PointIdsList(points=[str(point.projection_id)]), wait=True)
        issues = await store.reconcile()
        assert len(issues) == 1 and issues[0].record_id == point.id
        await store.stage(point)
        with pytest.raises(DomainError) as immutable:
            await store.stage(point.model_copy(update={"vector": (0.0, 1.0, 0.0)}))
        assert immutable.value.code == "VERSION_CONFLICT"
    finally:
        await vectors.delete_collection(collection)
        await graph.execute_query(
            "MATCH (n:ScopedProjection {org_id:$org,kg_id:$kg}) DETACH DELETE n",
            org=str(scope.org_id),
            kg=str(scope.kg_id),
        )
