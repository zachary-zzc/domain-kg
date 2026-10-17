"""Operational audit discloses only current owner-authorized identifiers and outcomes."""

import asyncio
from uuid import uuid4

import pytest

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.jobs.queue import JobQueue
from domain_kg.materials.models import AddMaterial
from domain_kg.models import PageRequest
from domain_kg.operations.audit import AuditTrail
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import BackupCommand, ExportCommand
from domain_kg.retrieval.models import SearchFilters

pytestmark = pytest.mark.storage


async def test_redacted_audit_controls_paging_and_current_owner_only(material_service, team):
    materials, kg = material_service
    queue = JobQueue(team[1], materials.router)
    await queue.initialize()
    operations = OwnerOperations(queue)
    await operations.initialize()
    owner = await team[1].resolve_access(team[2]["owner"], [kg], Action.CONFIGURE)
    command = ExportCommand(
        kg_id=kg,
        expected_published_revision=0,
        filters=SearchFilters(tags_any=("PRIVATE_AUDIT_POISON",)),
        idempotency_key="audit-export",
    )
    receipt = await operations.submit(owner, command)
    await queue.cancel(owner, receipt.job_id, idempotency_key="audit-cancel")
    await queue.cancel(owner, receipt.job_id, idempotency_key="audit-cancel")
    await queue.retry(owner, receipt.job_id, idempotency_key="audit-retry")
    trail = AuditTrail(queue)
    all_events = await trail.list(owner, kg, PageRequest())
    assert {event.outcome for event in all_events.items} == {"admitted", "cancelled", "retried"}
    assert len(all_events.items) == 3
    assert "PRIVATE_AUDIT_POISON" not in all_events.model_dump_json()
    assert all(event.job_id == receipt.job_id for event in all_events.items)
    first = await trail.list(owner, kg, PageRequest(limit=1))
    assert first.next_cursor and len(first.items) == 1
    second = await trail.list(owner, kg, PageRequest(limit=1, cursor=first.next_cursor))
    assert second.items[0].id != first.items[0].id
    reader = await team[1].resolve_access(team[2]["reader"], [kg], Action.READ)
    with pytest.raises(DomainError):
        await trail.list(reader, kg, PageRequest())
    with pytest.raises(DomainError):
        await trail.list(owner, uuid4(), PageRequest())


async def test_concurrent_operation_replay_and_wakeup_acknowledgement(material_service, team):
    materials, kg = material_service
    queue = JobQueue(team[1], materials.router)
    await queue.initialize()
    operations = OwnerOperations(queue)
    await operations.initialize()
    owner = await team[1].resolve_access(team[2]["owner"], [kg], Action.BACKUP)
    command = BackupCommand(
        kg_id=kg, expected_published_revision=0, idempotency_key="concurrent-owner-op"
    )
    first, replay = await asyncio.gather(
        operations.submit(owner, command), operations.submit(owner, command)
    )
    assert first == replay
    ns = {"org_id": str(owner.org_id), "kg_id": str(kg)}
    assert await queue.database.jobs.count_documents(ns) == 1
    assert await queue.database.operation_audit.count_documents(ns) == 1
    leased = await queue.claim("operation-wakeup", kinds=("backup",))
    assert leased.id == first.job_id
    wakeup = await queue.database.outbox.find_one(ns | {"job_id": str(first.job_id)})
    assert wakeup and wakeup["delivered"] is True


async def test_operation_key_collision_with_existing_source_is_an_intent_conflict(
    material_service, team
):
    materials, kg = material_service
    queue = JobQueue(team[1], materials.router)
    await queue.initialize()
    operations = OwnerOperations(queue)
    await operations.initialize()
    owner = await team[1].resolve_access(team[2]["owner"], [kg], Action.INGEST)
    await materials.add(
        owner,
        AddMaterial(
            kg_id=kg,
            title="Existing source command",
            inline_text="Tiny pending source",
            idempotency_key="source-and-owner-key",
        ),
    )
    with pytest.raises(DomainError) as conflict:
        await operations.submit(
            owner,
            BackupCommand(
                kg_id=kg, expected_published_revision=0, idempotency_key="source-and-owner-key"
            ),
        )
    assert conflict.value.code == ErrorCode.IDEMPOTENCY_CONFLICT


async def test_owner_work_cannot_fall_through_to_an_ingestion_pipeline(material_service, team):
    from domain_kg.ingestion.pipeline import Pipeline, PipelineStep
    from domain_kg.jobs.runner import JobRunner

    materials, kg = material_service
    queue = JobQueue(team[1], materials.router)
    await queue.initialize()
    operations = OwnerOperations(queue)
    await operations.initialize()
    owner = await team[1].resolve_access(team[2]["owner"], [kg], Action.BACKUP)
    receipt = await operations.submit(
        owner,
        BackupCommand(
            kg_id=kg, expected_published_revision=0, idempotency_key="no-source-fallback"
        ),
    )
    leased = await queue.claim("no-source-fallback", kinds=("backup",))

    async def source_only(job):
        pytest.fail("a non-source operation must never run a source pipeline step")

    await JobRunner(
        queue,
        Pipeline(
            steps=(PipelineStep(stage="validating", input_hash="0" * 64, execute=source_only),)
        ),
    ).run_job(leased)
    status = await queue.get(owner, receipt.job_id)
    assert status.stage == "repair_required" and "REVISION_NOT_READY" in status.warnings
