"""Restricted ACLs intersect KG grants and current source visibility."""

import asyncio
from uuid import uuid4

import pytest

from domain_kg.errors import DomainError
from domain_kg.identity.models import Action
from domain_kg.models import Namespace

pytestmark = pytest.mark.storage


async def test_immutable_eligible_scopes_and_visibility_rechecks(
    team, mongo_db, graph, vectors
) -> None:
    from datetime import UTC, datetime

    from domain_kg.engine.scope import EligibilityResolver, ScopePredicates
    from domain_kg.models import SourceVersion
    from domain_kg.retrieval.models import SearchRequest
    from domain_kg.revisions.models import PublishedView

    _, policy, principals, members, org, kg = team
    sources = [
        SourceVersion(org_id=org, kg_id=kg, material_id=uuid4(), version_id=uuid4())
        for _ in range(2)
    ]
    for index, source in enumerate(sources):
        await mongo_db.material_access.insert_one(
            {
                "org_id": str(org),
                "kg_id": str(kg),
                "id": str(source.material_id),
                "acl_mode": "kg" if index == 0 else "restricted",
                "member_ids": [str(members["editor"].id)],
                "group_ids": [],
                "active_version_id": str(source.version_id),
                "metadata": {
                    "material_id": str(source.material_id),
                    "version_id": str(source.version_id),
                    "media_type": "text/plain",
                    "tags": ["design"],
                    "created_at": datetime.now(UTC).isoformat(),
                    "updated_at": datetime.now(UTC).isoformat(),
                },
            }
        )
    view = PublishedView(org_id=org, kg_id=kg, revision=1, generation=uuid4(), sources=sources)

    class Views:
        async def load(self, access, kg_id):
            return view

    resolver = EligibilityResolver(policy, Views())
    request = SearchRequest(kg_ids=[kg], query="q", filters={"tags_all": ["design"]})
    access = [
        await policy.resolve_access(principals[name], [kg], Action.SEARCH)
        for name in ("reader", "editor")
    ]
    scopes = await asyncio.gather(
        resolver.resolve(access[0], request, kg),
        resolver.resolve(access[1], request, kg),
        resolver.resolve(access[0], request, kg),
    )
    assert [len(scope.visible_sources) for scope in scopes] == [1, 2, 1]
    assert ScopePredicates(scopes[0]).cache_key != ScopePredicates(scopes[1]).cache_key
    assert ScopePredicates(scopes[0]).graph_parameters["version_ids"] == [
        str(sources[0].version_id)
    ]
    from qdrant_client import models

    collection = f"scope_{uuid4().hex}"
    await vectors.create_collection(
        collection, vectors_config=models.VectorParams(size=3, distance=models.Distance.COSINE)
    )
    try:
        points = []
        for source in sources:
            payload = {
                "org_id": str(org),
                "kg_id": str(kg),
                "generation": str(view.generation),
                "material_id": str(source.material_id),
                "source_version_id": str(source.version_id),
            }
            points.append(
                models.PointStruct(id=str(uuid4()), vector=[1.0, 0.0, 0.0], payload=payload)
            )
            await graph.execute_query(
                "CREATE (n:PolicyScopeProbe) SET n += $payload", payload=payload
            )
        await vectors.upsert(collection, points=points, wait=True)

        async def inspect(scope):
            predicate = ScopePredicates(scope)
            found = await vectors.query_points(
                collection, query=[1.0, 0.0, 0.0], query_filter=predicate.vector_filter, limit=10
            )
            rows, _, _ = await graph.execute_query(
                "MATCH (n:PolicyScopeProbe {org_id:$org_id,kg_id:$kg_id,generation:$generation}) "
                "WHERE ANY(p IN $source_pairs WHERE p.material_id=n.material_id "
                "AND p.version_id=n.source_version_id) RETURN n.material_id AS id",
                **predicate.graph_parameters,
            )
            return len(found.points), len(rows)

        assert await asyncio.gather(*(inspect(scope) for scope in scopes)) == [
            (1, 1),
            (2, 2),
            (1, 1),
        ]
        empty = scopes[0].model_copy(update={"visible_sources": (), "visible_curations": ()})
        assert await inspect(empty) == (0, 0)
    finally:
        await vectors.delete_collection(collection)
        await graph.execute_query(
            "MATCH (n:PolicyScopeProbe {org_id:$org,kg_id:$kg}) DETACH DELETE n",
            org=str(org),
            kg=str(kg),
        )
    await mongo_db.kgs.update_one(
        {"org_id": str(org), "id": str(kg)}, {"$inc": {"visibility_revision": 1}}
    )
    with pytest.raises(DomainError):
        await resolver.revalidate(scopes[0])


async def test_restricted_material_intersection_injection_and_interleaved_clients(
    team, mongo_db
) -> None:
    from domain_kg.storage.integrity import (
        MaterialRecord,
        NamespaceRepository,
        install_mongo_schema,
    )
    from domain_kg.storage.mongo import ScopedMongoRepository

    identities, policy, principals, members, org, kg = team
    await install_mongo_schema(mongo_db)
    identifier, foreign = uuid4(), uuid4()
    canonical = NamespaceRepository(mongo_db, Namespace(org_id=org, kg_id=kg))
    await canonical.insert(MaterialRecord(org_id=org, kg_id=kg, id=identifier, title="Secret"))
    await mongo_db.material_access.insert_one(
        {
            "org_id": str(org),
            "kg_id": str(kg),
            "id": str(identifier),
            "acl_mode": "restricted",
            "member_ids": [str(members["editor"].id), str(members["denied"].id)],
            "group_ids": [],
            "excluded": False,
            "tombstoned": False,
        }
    )
    contexts = {
        name: await policy.resolve_access(principals[name], [kg], Action.READ)
        for name in ("reader", "editor", "owner")
    }
    repositories = {
        name: ScopedMongoRepository(mongo_db, policy, context, kg)
        for name, context in contexts.items()
    }
    results = await asyncio.gather(
        *(
            repositories[name].get_material(identifier)
            for name in ("reader", "editor", "reader", "owner")
        ),
        return_exceptions=True,
    )
    assert isinstance(results[0], DomainError) and isinstance(results[2], DomainError)
    assert results[1].title == "Secret" and results[3].title == "Secret"
    with pytest.raises(DomainError):
        await policy.resolve_access(principals["denied"], [kg], Action.READ)
    with pytest.raises(DomainError):
        await repositories["editor"].get_material({"$ne": None})
    with pytest.raises(DomainError):
        await repositories["editor"].get_material(foreign)
    # Visibility fencing must defeat a previously resolved context.
    owner = await policy.resolve_access(principals["owner"], [kg], Action.MANAGE_GRANTS)
    await policy.set_material_acl(
        owner,
        kg,
        identifier,
        "restricted",
        member_ids=(),
        group_ids=(),
        idempotency_key="remove-editor",
    )
    with pytest.raises(DomainError):
        await repositories["editor"].get_material(identifier)
    assert (
        await policy.resolve_access(principals["reader"], [kg], Action.READ)
    ).policy_revision > contexts["reader"].policy_revision
