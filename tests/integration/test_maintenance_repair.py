"""Repair publishes new verified projections without changing accepted human intent."""
# ruff: noqa: F811

import pytest
from qdrant_client import models
from test_maintenance_admission import accepted_human_edit
from test_publication import publishing  # noqa: F401

from domain_kg.errors import DomainError
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import MaintenanceCommand
from domain_kg.revisions.projections import AcceptedProjection
from domain_kg.revisions.publication import accepted_document, projection_rows, verify_accepted

pytestmark = pytest.mark.storage


async def test_repair_replaces_broken_curation_without_replaying_the_human_edit(publishing, team):
    from domain_kg.operations.maintenance import MaintenanceService

    source, alpha, curation = await accepted_human_edit(publishing)
    _, editor, queue, _, _, _ = publishing
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.CONFIGURE)
    ns = {"org_id": str(source.org_id), "kg_id": str(source.kg_id)}
    history = await queue.database.curation_revisions.find_one(
        ns | {"id": str(curation.revision_id)}
    )
    counters = await queue.database.curation_targets.find(ns).to_list()
    prior_origin = None
    for revision in (2, 3):
        kg = await queue.database.kgs.find_one({"id": str(source.kg_id)})
        row = await queue.database.publication_bindings.find_one(
            ns
            | {
                "view_manifest_id": kg["published_manifest_id"],
                "binding.curation.id": str(curation.revision_id),
            }
        )
        parent = AcceptedProjection.model_validate(row["binding"])
        document, batch, _ = await accepted_document(queue, owner, parent)
        stores = await queue.router.for_kg(owner, source.kg_id)
        await stores.vectors.client.delete(
            stores.vectors.collection,
            points_selector=models.PointIdsList(
                points=[row["projection_id"] for row in projection_rows(document, batch)]
            ),
            wait=True,
        )
        with pytest.raises(DomainError):
            await verify_accepted(queue, owner, parent)
        receipt = await OwnerOperations(queue).submit(
            owner,
            MaintenanceCommand(
                kg_id=source.kg_id,
                expected_published_revision=revision,
                curation_id=curation.revision_id,
                idempotency_key=f"repair-{revision}",
            ),
        )
        job = await queue.claim("actual-repair-owner", kinds=("maintenance",))
        service = MaintenanceService(queue)
        await service.run(job)
        assert await queue.accepted(job)
        await service.run(job)  # lost completion response
        assert (await queue.get(owner, receipt.job_id)).stage == "ready"
        changed = await GraphService(queue).get_node(editor, source.kg_id, alpha)
        assert (
            changed.name == "Reviewed Alpha"
            and changed.curations[0].provenance.reason == history["provenance"]["reason"]
        )
        latest = await queue.database.kgs.find_one({"id": str(source.kg_id)})
        assert latest["published_revision"] == revision + 1
        active = await queue.database.publication_bindings.find(
            ns
            | {"view_manifest_id": latest["published_manifest_id"], "owner_key": row["owner_key"]}
        ).to_list()
        assert len(active) == 1 and active[0]["binding"]["manifest_id"] != str(parent.manifest_id)
        attempt = await queue.database.projection_attempts.find_one(
            ns | {"id": latest["published_manifest_id"]}
        )
        if prior_origin:
            assert attempt["maintenance_origin"]["base"] == prior_origin
        prior_origin = attempt["maintenance_origin"]["base"]
        assert (
            await queue.database.curation_revisions.find_one(ns | {"id": str(curation.revision_id)})
            == history
        )
        assert await queue.database.curation_targets.find(ns).to_list() == counters
