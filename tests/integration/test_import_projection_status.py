"""Unverified provenance must not exempt an accepted import from readiness checks."""

import pytest
from qdrant_client import models
from test_import_recovery import imported

from domain_kg.identity.models import Action
from domain_kg.operations.projections import ProjectionManagement

pytestmark = pytest.mark.storage


async def test_owner_readiness_checks_imported_vectors_without_promoting_trust(
    material_service, team
):
    fixture, view = await imported(material_service, team)
    _, editor, queue, _, _, _ = fixture
    source = view.sources[0]
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.CONFIGURE)
    service = ProjectionManagement(queue)
    status = await service.status(owner, source.kg_id)
    assert status.source_count == 1 and status.vector_ready and status.graph_ready
    vectors = await queue.router.for_vectors(editor, source.kg_id)
    points, _ = await vectors.client.scroll(
        vectors.collection,
        scroll_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="org_id", match=models.MatchValue(value=str(source.org_id))
                ),
                models.FieldCondition(
                    key="kg_id", match=models.MatchValue(value=str(source.kg_id))
                ),
                models.FieldCondition(
                    key="attempt_id",
                    match=models.MatchValue(value=str(view.projections[0].attempt_id)),
                ),
            ]
        ),
        limit=1,
    )
    assert points
    await vectors.client.delete(
        vectors.collection, points_selector=models.PointIdsList(points=[points[0].id]), wait=True
    )
    broken = await service.status(owner, source.kg_id)
    assert broken.state == "repair_required" and not broken.vector_ready and broken.graph_ready
