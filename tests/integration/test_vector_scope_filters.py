"""Large eligibility maps stay paired, with tiny real Qdrant payloads."""

from uuid import uuid4

import pytest
from qdrant_client import models

from domain_kg.engine.scope import ScopePredicates
from domain_kg.identity.models import Action
from domain_kg.models import SourceVersion
from domain_kg.revisions.models import PublishedView, ReadScope
from domain_kg.revisions.projections import AcceptedProjection

pytestmark = pytest.mark.storage


async def test_thousand_source_map_rejects_cross_products_and_wrong_generations(team, vectors):
    _, policy, principals, _, org, kg = team
    access = await policy.resolve_access(principals["reader"], [kg], Action.SEARCH)
    generation = uuid4()
    sources = tuple(
        SourceVersion(org_id=org, kg_id=kg, material_id=uuid4(), version_id=uuid4())
        for _ in range(1000)
    )
    bindings = tuple(
        AcceptedProjection(
            org_id=org,
            kg_id=kg,
            source=source,
            generation=generation,
            attempt_id=uuid4(),
            manifest_id=uuid4(),
            content_hash="a" * 64,
            chunk_artifact_id=uuid4(),
        )
        for source in sources
    )
    scope = ReadScope(
        access=access,
        visibility_revision=0,
        visible_sources=sources[:-1],
        view=PublishedView(
            org_id=org,
            kg_id=kg,
            revision=1,
            generation=generation,
            sources=sources,
            projections=bindings,
        ),
    )

    def payload(index):
        source, binding = sources[index], bindings[index]
        return {
            "org_id": str(org),
            "kg_id": str(kg),
            "generation": str(generation),
            "material_id": str(source.material_id),
            "source_version_id": str(source.version_id),
            "attempt_id": str(binding.attempt_id),
            "manifest_id": str(binding.manifest_id),
            "manifest_hash": binding.content_hash,
        }

    valid_first, valid_last = str(uuid4()), str(uuid4())
    points = [
        models.PointStruct(id=valid_first, vector=[0.8, 0.2, 0.0], payload=payload(0)),
        models.PointStruct(id=valid_last, vector=[1.0, 0.0, 0.0], payload=payload(998)),
    ]
    for changes in (
        payload(999),
        payload(0) | {"attempt_id": str(uuid4())},
        payload(0)
        | {
            "material_id": str(sources[1].material_id),
            "source_version_id": str(sources[1].version_id),
        },
        payload(0) | {"generation": str(uuid4())},
        payload(0) | {"org_id": str(uuid4())},
        payload(0) | {"kg_id": str(uuid4())},
        payload(0) | {"manifest_hash": "b" * 64},
    ):
        points.append(models.PointStruct(id=str(uuid4()), vector=[1.0, 0.0, 0.0], payload=changes))
    collection = f"large_scope_{uuid4().hex}"
    await vectors.create_collection(
        collection, vectors_config=models.VectorParams(size=3, distance=models.Distance.COSINE)
    )
    try:
        await vectors.upsert(collection, points=points, wait=True)
        result = await vectors.query_points(
            collection,
            query=[1.0, 0.0, 0.0],
            query_filter=ScopePredicates(scope).vector_filter,
            search_params=models.SearchParams(exact=True),
            limit=50,
        )
        assert [str(point.id) for point in result.points] == [valid_last, valid_first]
    finally:
        await vectors.delete_collection(collection)
