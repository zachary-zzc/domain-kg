"""Job pagination filters current ACLs before its visible page and binds opaque cursors."""

from uuid import uuid4

import pytest

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.jobs.inspection import JobInspection
from domain_kg.jobs.queue import JobQueue
from domain_kg.materials.models import AddMaterial
from domain_kg.models import PageRequest

pytestmark = pytest.mark.storage


async def test_job_pages_skip_denied_jobs_and_expire_on_policy_or_actor_change(
    material_service, team
):
    materials, kg = material_service
    policy, principals = team[1], team[2]
    editor = await policy.resolve_access(principals["editor"], [kg], Action.INGEST)
    receipts = [
        await materials.add(
            editor,
            AddMaterial(
                kg_id=kg,
                title=f"Source {index}",
                inline_text=f"Source body {index}",
                idempotency_key=f"job-page-{index}",
            ),
        )
        for index in range(3)
    ]
    receipts.sort(key=lambda receipt: str(receipt.job_id))
    owner = await policy.resolve_access(principals["owner"], [kg], Action.MANAGE_GRANTS)
    await policy.set_material_acl(
        owner,
        kg,
        receipts[0].material_id,
        "restricted",
        member_ids=(),
        group_ids=(),
        idempotency_key="hide-first-job",
    )
    reader = await policy.resolve_access(principals["reader"], [kg], Action.READ)
    queue = JobQueue(policy, materials.router)
    await queue.initialize()
    service = JobInspection(queue)
    first = await service.list(reader, kg, PageRequest(limit=1))
    assert [item.job_id for item in first.items] == [receipts[1].job_id]
    assert first.next_cursor and str(receipts[0].job_id) not in first.model_dump_json()
    second = await service.list(reader, kg, PageRequest(limit=1, cursor=first.next_cursor))
    assert [item.job_id for item in second.items] == [receipts[2].job_id]
    assert second.next_cursor is None
    editor = await policy.resolve_access(principals["editor"], [kg], Action.READ)
    with pytest.raises(DomainError) as actor:
        await service.list(editor, kg, PageRequest(limit=1, cursor=first.next_cursor))
    assert actor.value.code == ErrorCode.CURSOR_EXPIRED
    owner = await policy.resolve_access(principals["owner"], [kg], Action.MANAGE_GRANTS)
    await policy.set_material_acl(
        owner,
        kg,
        receipts[2].material_id,
        "restricted",
        member_ids=(),
        group_ids=(),
        idempotency_key="hide-next-job",
    )
    reader = await policy.resolve_access(principals["reader"], [kg], Action.READ)
    with pytest.raises(DomainError) as expired:
        await service.list(reader, kg, PageRequest(limit=1, cursor=first.next_cursor))
    assert expired.value.code == ErrorCode.CURSOR_EXPIRED


@pytest.mark.parametrize("reference", ["missing", "other_material"])
async def test_corrupt_job_version_reference_is_a_consistency_failure(
    material_service, team, reference
):
    materials, kg = material_service
    editor = await team[1].resolve_access(team[2]["editor"], [kg], Action.INGEST)
    receipt = await materials.add(
        editor,
        AddMaterial(
            kg_id=kg,
            title="Job reference",
            inline_text="Synthetic source",
            idempotency_key="job-reference",
        ),
    )
    target = uuid4()
    if reference == "other_material":
        other = await materials.add(
            editor,
            AddMaterial(
                kg_id=kg,
                title="Other source",
                inline_text="Independent source",
                idempotency_key="other-job-reference",
            ),
        )
        target = other.version_id
    await materials.database.jobs.update_one(
        {"org_id": str(editor.org_id), "kg_id": str(kg), "id": str(receipt.job_id)},
        {"$set": {"target_id": str(target)}},
    )
    reader = await team[1].resolve_access(team[2]["reader"], [kg], Action.READ)
    queue = JobQueue(team[1], materials.router)
    await queue.initialize()
    for read in (
        lambda: queue.get(reader, receipt.job_id),
        lambda: JobInspection(queue).list(reader, kg, PageRequest()),
    ):
        with pytest.raises(DomainError) as error:
            await read()
        assert error.value.code == ErrorCode.PROJECTION_INCONSISTENT
