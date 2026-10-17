"""Real expired packages are erased with live bytes and current policy protected."""
# ruff: noqa: F811

import asyncio
from datetime import timedelta

import pytest
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.jobs.queue import now
from domain_kg.operations.export import ExportService
from domain_kg.operations.export_models import ExportStore
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import ExportCommand, RetentionCommand
from domain_kg.operations.retention import RetentionService

pytestmark = pytest.mark.storage


async def test_expired_export_cleanup_is_scoped_replayable_and_resumes_after_revocation(
    publishing, team, monkeypatch
):
    _, editor, queue, coordinator, _, _ = publishing
    source, source_job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, source_job, checkpoint))
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.CONFIGURE)
    operations = OwnerOperations(queue)
    expired_at = now() - timedelta(days=3)
    monkeypatch.setattr("domain_kg.operations.export.now", lambda: expired_at)
    await operations.submit(
        owner,
        ExportCommand(
            kg_id=source.kg_id, expected_published_revision=1, idempotency_key="expired-export"
        ),
    )
    exported = await queue.claim("export", kinds=("export",))
    await ExportService(queue).run(exported)
    row = await queue.database.export_results.find_one({"id": str(exported.target_id)})
    key_ref = row["content"]
    from domain_kg.operations.export_models import ExportObject

    reference = ExportObject.model_validate(key_ref).retained
    binding = (await queue.router.for_sources(owner, source.kg_id)).blobs
    store = ExportStore(binding)
    live = f"{owner.org_id}/{source.kg_id}/original/keep"
    await asyncio.to_thread(
        binding.client.put_object, Bucket=binding.bucket, Key=live, Body=b"live"
    )
    command = RetentionCommand(
        kg_id=source.kg_id,
        expected_published_revision=1,
        resources="exports",
        idempotency_key="cleanup-export",
    )
    receipt = await operations.submit(owner, command)
    assert await operations.submit(owner, command) == receipt
    plan = await queue.database.retention_plans.find_one({"job.id": str(receipt.job_id)})
    assert len(plan["targets"]) == 1
    second = await operations.submit(
        owner, command.model_copy(update={"idempotency_key": "concurrent-cleanup"})
    )
    other_plan = await queue.database.retention_plans.find_one({"job.id": str(second.job_id)})
    assert other_plan["targets"] == []
    job = await queue.claim("retention", kinds=("retention",))
    assert job.id == receipt.job_id
    service = RetentionService(queue)
    erase = service._erase

    async def revoke_after_erasure(*args):
        await erase(*args)
        await queue.database.members.update_one(
            {"id": str(team[3]["owner"].id)}, {"$set": {"suspended": True}}
        )

    monkeypatch.setattr(service, "_erase", revoke_after_erasure)
    with pytest.raises(DomainError):
        await service.run(job)
    assert not await queue.database.retention_results.find_one({"id": str(job.target_id)})
    await queue.database.members.update_one(
        {"id": str(team[3]["owner"].id)}, {"$set": {"suspended": False}}
    )
    monkeypatch.setattr(service, "_erase", erase)
    await queue.cancel(owner, job.id, idempotency_key="cancel-partial-cleanup")
    await queue.retry(owner, job.id, idempotency_key="retry-partial-cleanup")
    with pytest.raises(DomainError):
        await service.run(job)
    previous_attempt = job.attempt_id
    job = await queue.claim("retention-retry", kinds=("retention",))
    if job.id == second.job_id:
        await service.run(job)
        job = await queue.claim("retention-retry", kinds=("retention",))
    assert job.id == receipt.job_id and job.attempt_id != previous_attempt
    await service.run(job)
    assert await queue.accepted(job)
    await queue.finish(job)
    await service.run(job)
    assert (
        len(await queue.database.retention_results.find({"id": str(job.target_id)}).to_list()) == 1
    )
    with pytest.raises(DomainError) as gone:
        await anext(store.open(reference))
    assert gone.value.code == ErrorCode.BACKEND_UNAVAILABLE
    response = await asyncio.to_thread(binding.client.get_object, Bucket=binding.bucket, Key=live)
    try:
        assert await asyncio.to_thread(response["Body"].read) == b"live"
    finally:
        response["Body"].close()
    assert (await queue.database.kgs.find_one({"id": str(source.kg_id)}))["published_revision"] == 1
    assert await queue.database.operation_audit.find_one(
        {"job_id": str(job.id), "outcome": "completed"}
    )
