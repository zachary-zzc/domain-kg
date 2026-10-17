"""Projection loss does not erase a curation's sealed, independently authored history."""
# ruff: noqa: F811

import pytest
from qdrant_client import models
from test_maintenance_admission import accepted_human_edit
from test_publication import publishing  # noqa: F401

from domain_kg.identity.models import Action
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import MaintenanceCommand
from domain_kg.revisions.publication import accepted_document, projection_rows

pytestmark = pytest.mark.storage


async def test_curation_repair_reads_sealed_history_after_projection_loss(publishing, team):
    from domain_kg.operations.maintenance_preparation import MaintenancePreparation

    source, _, receipt = await accepted_human_edit(publishing)
    _, _, queue, _, _, _ = publishing
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.CONFIGURE)
    admitted = await OwnerOperations(queue).submit(
        owner,
        MaintenanceCommand(
            kg_id=source.kg_id,
            expected_published_revision=2,
            curation_id=receipt.revision_id,
            idempotency_key="prepare-human-repair",
        ),
    )
    target = await queue.database.maintenance_targets.find_one(
        {"org_id": str(source.org_id), "kg_id": str(source.kg_id), "job.id": str(admitted.job_id)}
    )
    from domain_kg.revisions.projections import AcceptedProjection

    parent = AcceptedProjection.model_validate(target["parent"])
    document, batch, _ = await accepted_document(queue, owner, parent)
    stores = await queue.router.for_kg(owner, source.kg_id)
    await stores.vectors.client.delete(
        stores.vectors.collection,
        points_selector=models.PointIdsList(
            points=[row["projection_id"] for row in projection_rows(document, batch)]
        ),
        wait=True,
    )
    job = await queue.claim("prepare-maintenance", kinds=("maintenance",))
    prepared = await MaintenancePreparation(queue).prepare(job)
    assert prepared.document == document and prepared.target.parent == parent
    assert prepared.document.directive.provenance.reason == "Keep this original human intent"
    assert prepared.document.directive.provenance.actor != f"member:{team[3]['owner'].id}"
    assert not await queue.accepted(job)
    assert (await queue.database.kgs.find_one({"id": str(source.kg_id)}))["published_revision"] == 2
