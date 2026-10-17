"""A rebuild is an actual scoped owner job, never an ingest or historical curator."""
# ruff: noqa: F811

from uuid import UUID

import pytest
from test_maintenance_admission import accepted_human_edit
from test_publication import publishing  # noqa: F401

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.operations.jobs import OwnerOperations

pytestmark = pytest.mark.storage


async def test_generation_admission_pins_exact_bindings_and_refuses_unavailable_sources(
    publishing,
    team,
):
    from domain_kg.operations.models import GenerationCommand

    source, _, _ = await accepted_human_edit(publishing)
    _, editor, queue, _, _, _ = publishing
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.CONFIGURE)
    command = GenerationCommand(
        kg_id=source.kg_id,
        expected_published_revision=2,
        idempotency_key="generation-current-space",
    )
    operations = OwnerOperations(queue)
    with pytest.raises(DomainError):
        await operations.submit(editor, command)
    receipt = await operations.submit(owner, command)
    assert await operations.submit(owner, command) == receipt
    ns = {"org_id": str(source.org_id), "kg_id": str(source.kg_id)}
    target = await queue.database.generation_targets.find_one(ns | {"job.id": str(receipt.job_id)})
    assert target is not None
    kg = await queue.database.kgs.find_one({"org_id": ns["org_id"], "id": ns["kg_id"]})
    assert target["generation"] != kg["generation"]
    assert target["binding_count"] == 2
    assert (
        await queue.database.generation_inputs.count_documents(ns | {"operation_id": target["id"]})
        == 2
    )
    assert await queue.claim("ordinary-source-worker") is None
    job = await queue.claim("generation-owner", kinds=("generation",))
    assert job.id == receipt.job_id and job.target_id == UUID(target["id"])
    assert not await queue.accepted(job)
    with pytest.raises(DomainError):
        await queue.finish(job)
    await queue.cancel(owner, job.id, idempotency_key="cancel-generation")
    await queue.database.material_access.update_one(
        ns | {"id": str(source.material_id)}, {"$set": {"purged": True}}
    )
    with pytest.raises(DomainError) as purged:
        await operations.submit(
            owner, command.model_copy(update={"idempotency_key": "purged-generation"})
        )
    assert purged.value.code == ErrorCode.REVISION_NOT_READY
    assert await queue.database.generation_targets.count_documents(ns) == 1
