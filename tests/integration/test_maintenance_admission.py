"""Owner maintenance pins an accepted curation, not a caller-selected source version."""
# ruff: noqa: F811

from uuid import uuid4

import pytest
from test_curation import alpha_id, publish_curation
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import MaintenanceCommand
from domain_kg.revisions.models import NodeChanges, NodeUpdate
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


async def accepted_human_edit(publishing):
    _, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    alpha = await alpha_id(queue, editor, job.kg_id)
    receipt = await RevisionService(queue, coordinator.providers).update_node(
        editor,
        NodeUpdate(
            kg_id=job.kg_id,
            entity_id=alpha,
            expected_revision=0,
            source_ids=(source.material_id,),
            changes=NodeChanges(name="Reviewed Alpha"),
            reason="Keep this original human intent",
            idempotency_key="maintenance-human-edit",
        ),
    )
    await publish_curation(queue, coordinator, editor, receipt)
    return source, alpha, receipt


async def test_maintenance_admission_requires_owner_and_exact_accepted_parent(publishing, team):
    source, _, curation = await accepted_human_edit(publishing)
    _, editor, queue, _, _, _ = publishing
    operations = OwnerOperations(queue)
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.CONFIGURE)
    command = MaintenanceCommand(
        kg_id=source.kg_id,
        expected_published_revision=2,
        curation_id=curation.revision_id,
        idempotency_key="owner-curation-repair",
    )
    with pytest.raises(DomainError):
        await operations.submit(editor, command)
    reader = await queue.policy.resolve_access(team[2]["reader"], [source.kg_id], Action.READ)
    with pytest.raises(DomainError):
        await operations.submit(reader, command)
    with pytest.raises(DomainError):
        await operations.submit(
            owner,
            command.model_copy(
                update={"curation_id": uuid4(), "idempotency_key": "unknown-curation"}
            ),
        )
    with pytest.raises(DomainError) as stale:
        await operations.submit(
            owner,
            command.model_copy(
                update={"expected_published_revision": 1, "idempotency_key": "stale-curation"}
            ),
        )
    assert stale.value.code == ErrorCode.VERSION_CONFLICT
    receipt = await operations.submit(owner, command)
    assert await operations.submit(owner, command) == receipt
    ns = {"org_id": str(source.org_id), "kg_id": str(source.kg_id)}
    target = await queue.database.maintenance_targets.find_one(ns | {"job.id": str(receipt.job_id)})
    assert target is not None and target["parent"]["curation"]["id"] == str(curation.revision_id)
    assert target["parent"]["generation"] == str(
        (await queue.database.kgs.find_one({"id": str(source.kg_id)}))["generation"]
    )
    assert await queue.claim("ordinary-worker") is None
    job = await queue.claim("maintenance-worker", kinds=("maintenance",))
    assert job.id == receipt.job_id and job.target_id != curation.revision_id
    assert not await queue.accepted(job)
    with pytest.raises(DomainError):
        await queue.finish(job)
