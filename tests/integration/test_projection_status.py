"""Owner status verifies accepted graph/vector receipts without broadening material ACLs."""
# ruff: noqa: F811

import pytest
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError
from domain_kg.identity.models import Action
from domain_kg.operations.projections import ProjectionManagement

pytestmark = pytest.mark.storage


async def test_owner_projection_status_and_explicit_repair_jobs(publishing, team):
    _, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    owner = await queue.policy.resolve_access(team[2]["owner"], [job.kg_id], Action.CONFIGURE)
    service = ProjectionManagement(queue)
    with pytest.raises(DomainError):
        await service.status(editor, job.kg_id)
    status = await service.status(owner, job.kg_id)
    assert status.kg_id == job.kg_id and status.published_revision == 1
    assert status.state == "ready" and status.source_count == 1
    assert status.graph_ready and status.vector_ready
    receipt = await service.repair(
        owner, job.kg_id, source.material_id, idempotency_key="owner-repair"
    )
    assert receipt.job_id and receipt.stage == "queued"
    row = await queue.database.jobs.find_one(
        {"org_id": str(owner.org_id), "kg_id": str(job.kg_id), "id": str(receipt.job_id)}
    )
    assert row["kind"] == "repair" and row["target_id"] == str(source.version_id)
    assert (
        await service.repair(owner, job.kg_id, source.material_id, idempotency_key="owner-repair")
        == receipt
    )
