"""Job pagination filters current ACLs before its visible page and binds opaque cursors."""

from uuid import uuid4

import pytest

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.jobs.inspection import JobFilters, JobInspection
from domain_kg.jobs.queue import JobQueue
from domain_kg.materials.models import AddMaterial
from domain_kg.models import PageRequest

pytestmark = pytest.mark.storage


async def test_job_views_filter_before_paging_and_bind_cursors(material_service, team):
    materials, kg = material_service
    access = await team[1].resolve_access(team[2]["editor"], [kg], Action.INGEST)
    receipts = [
        await materials.add(
            access,
            AddMaterial(
                kg_id=kg, title=f"View {i}", inline_text="Fixture", idempotency_key=f"view-{i}"
            ),
        )
        for i in range(4)
    ]
    queue = JobQueue(team[1], materials.router)
    await queue.initialize()
    await queue.cancel(access, receipts[1].job_id, idempotency_key="view-cancel-1")
    await queue.cancel(access, receipts[3].job_id, idempotency_key="view-cancel-3")
    inspection = JobInspection(queue)
    active = JobFilters(view="active", sources_only=True)
    first = await inspection.list(access, kg, PageRequest(limit=1), active)
    assert [item.job_id for item in first.items] == [receipts[2].job_id]
    second = await inspection.list(
        access, kg, PageRequest(limit=1, cursor=first.next_cursor), active
    )
    assert [item.job_id for item in second.items] == [receipts[0].job_id]
    attention = await inspection.list(access, kg, PageRequest(), JobFilters(view="attention"))
    assert [item.job_id for item in attention.items] == [receipts[3].job_id, receipts[1].job_id]
    with pytest.raises(DomainError) as changed:
        await inspection.list(
            access, kg, PageRequest(limit=1, cursor=first.next_cursor), JobFilters()
        )
    assert changed.value.code == ErrorCode.CURSOR_EXPIRED


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
    receipts.reverse()  # Newest accepted command first, independent of random UUID order.
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


@pytest.mark.parametrize(
    "code,detail",
    [
        (ErrorCode.QUOTA_EXCEEDED, "private provider detail"),
        (
            ErrorCode.EVIDENCE_VALIDATION_FAILED,
            "An extracted entity name is absent from its cited source passages.",
        ),
        (ErrorCode.PROVIDER_UNAVAILABLE, "Extraction exceeded its output budget."),
    ],
)
async def test_job_receipts_identify_the_source_and_explain_failures_without_raw_errors(
    material_service, team, code, detail
):
    materials, kg = material_service
    policy, principals = team[1], team[2]
    editor = await policy.resolve_access(principals["editor"], [kg], Action.INGEST)
    receipt = await materials.add(
        editor,
        AddMaterial(
            kg_id=kg,
            title="downloads/paper.txt",
            source="folder:downloads/paper.txt",
            inline_text="Synthetic source",
            idempotency_key="diagnosable-job",
        ),
    )
    queue = JobQueue(policy, materials.router)
    await queue.initialize()
    leased = await queue.claim("diagnostics")
    await queue.fail(leased, DomainError(code, detail))
    reader = await policy.resolve_access(principals["reader"], [kg], Action.READ)
    result = await queue.get(reader, receipt.job_id)
    assert result.source.title == "downloads/paper.txt"
    assert result.source.source == "folder:downloads/paper.txt"
    assert result.source.material_id == receipt.material_id
    assert result.source.version_id == receipt.version_id
    assert result.kind == "ingest" and result.created_at is not None
    assert result.error.code == str(code)
    assert result.error.stage == "validating"
    assert result.error.request_id is not None
    if code == ErrorCode.QUOTA_EXCEEDED:
        assert "budget" in result.error.action.lower()
    else:
        assert result.error.message == detail
    assert "private provider detail" not in result.model_dump_json()
    assert (await JobInspection(queue).list(reader, kg, PageRequest())).items[0] == result

    owner = await policy.resolve_access(principals["owner"], [kg], Action.MANAGE_GRANTS)
    await policy.set_material_acl(
        owner,
        kg,
        receipt.material_id,
        "restricted",
        member_ids=(),
        group_ids=(),
        idempotency_key="hide-diagnostic-source",
    )
    with pytest.raises(DomainError):
        await queue.get(reader, receipt.job_id)
    reader = await policy.resolve_access(principals["reader"], [kg], Action.READ)
    assert not (await JobInspection(queue).list(reader, kg, PageRequest())).items


async def test_corrupt_job_original_metadata_returns_a_safe_consistency_error(
    material_service, team
):
    materials, kg = material_service
    access = await team[1].resolve_access(team[2]["editor"], [kg], Action.INGEST)
    receipt = await materials.add(
        access,
        AddMaterial(
            kg_id=kg,
            title="Corrupt metadata",
            inline_text="Synthetic",
            idempotency_key="invalid-job-original",
        ),
    )
    stores = await materials.router.for_sources(access, kg)
    await stores.authority._database.version_blobs.update_one(
        {"org_id": str(access.org_id), "kg_id": str(kg), "id": str(receipt.version_id)},
        {"$set": {"blob.sha256": "invalid-private-store-value"}},
    )
    reader = await team[1].resolve_access(team[2]["reader"], [kg], Action.READ)
    queue = JobQueue(team[1], materials.router)
    with pytest.raises(DomainError) as failure:
        await queue.get(reader, receipt.job_id)
    assert failure.value.code == ErrorCode.PROJECTION_INCONSISTENT
    assert "invalid-private-store-value" not in failure.value.message
