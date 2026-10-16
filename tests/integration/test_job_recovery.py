"""Small real-Mongo crash, replay and publication-fence characterization."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from domain_kg.errors import DomainError
from domain_kg.identity.models import Action
from domain_kg.jobs.models import JobCommand
from domain_kg.materials.models import AddMaterial

pytestmark = pytest.mark.storage


@pytest.fixture
async def queued(material_service, team, mongo_db):
    from domain_kg.jobs.queue import JobQueue

    materials, kg = material_service
    _, policy, principals, _, org, _ = team
    access = await policy.resolve_access(principals["editor"], [kg], Action.INGEST)
    receipt = await materials.add(
        access,
        AddMaterial(kg_id=kg, inline_text="Durable work", title="Source", idempotency_key="source"),
    )
    queue = JobQueue(policy, materials.router, lease_seconds=30, max_attempts=3)
    await queue.initialize()
    command = JobCommand(
        org_id=org,
        kg_id=kg,
        id=receipt.job_id,
        kind="ingest",
        target_id=receipt.version_id,
        idempotency_key="source",
    )
    return queue, access, command, receipt


async def expire(database, job):
    await database.jobs.update_one(
        {"org_id": str(job.org_id), "kg_id": str(job.kg_id), "id": str(job.id)},
        {"$set": {"lease_until": datetime.now(UTC) - timedelta(seconds=1)}},
    )


async def test_duplicate_delivery_and_concurrent_claimants_are_fenced(queued, mongo_db):
    queue, access, command, _ = queued
    receipt = await queue.enqueue(access, command)
    assert await queue.enqueue(access, command) == receipt
    with pytest.raises(DomainError):
        await queue.enqueue(access, command.model_copy(update={"target_id": uuid4()}))
    assert await mongo_db.jobs.count_documents({}) == 1
    results = await asyncio.gather(*(queue.claim(f"worker-{index}") for index in range(4)))
    claimed = [job for job in results if job is not None]
    assert len(claimed) == 1
    first = claimed[0]
    await expire(mongo_db, first)
    second = await queue.claim("recovered")
    assert second and second.id == first.id and second.fence > first.fence
    assert second.attempt_id != first.attempt_id and second.lease_token != first.lease_token
    with pytest.raises(DomainError):
        await queue.heartbeat(first)
    assert (await queue.heartbeat(second)).attempt_id == second.attempt_id
    assert await mongo_db.outbox.count_documents({"delivered": True}) == 1


async def test_preparation_checkpoints_resume_but_projection_attempts_do_not(queued, mongo_db):

    queue, _, _, _ = queued
    first = await queue.claim("first")

    parsed = await queue.store_artifact(first, "parsing", "1" * 64, b'{"parsed":true}')
    projected = await queue.store_artifact(first, "projecting", "1" * 64, b'{"projected":true}')
    await queue.checkpoint(first, parsed)
    await queue.checkpoint(first, projected)
    with pytest.raises(DomainError):
        await queue.checkpoint(first, parsed.model_copy(update={"output_hash": "3" * 64}))
    await expire(mongo_db, first)
    recovered = await queue.claim("second")
    assert await queue.load_checkpoint(recovered, "parsing", "1" * 64) == parsed
    assert await queue.load_checkpoint(recovered, "parsing", "4" * 64) is None
    assert await queue.load_checkpoint(recovered, "projecting", "1" * 64) is None
    with pytest.raises(DomainError):
        await queue.checkpoint(first, parsed)
    with pytest.raises(DomainError):
        await queue.finish(recovered)


async def test_explicit_cancel_and_retry_exhaustion(queued, mongo_db):
    queue, access, command, _ = queued
    first = await queue.claim("first")
    result = await queue.cancel(access, first.id, idempotency_key="cancel")
    assert result.stage == "cancelled"
    assert await queue.cancel(access, first.id, idempotency_key="cancel") == result
    with pytest.raises(DomainError):
        await queue.heartbeat(first)
    assert await queue.claim("after-cancel") is None
    await queue.retry(access, command.id, idempotency_key="retry")
    for _ in range(3):
        job = await queue.claim("failing")
        assert job is not None
        await queue.fail(
            job, DomainError("BACKEND_UNAVAILABLE", "secret exception text", retryable=True)
        )
        await mongo_db.jobs.update_one(
            {"id": str(job.id)}, {"$set": {"available_at": datetime.now(UTC)}}
        )
    assert await queue.claim("exhausted") is None
    status = await queue.get(access, command.id)
    assert status.stage == "failed" and "secret exception text" not in status.model_dump_json()
    assert await mongo_db.jobs.count_documents({}) == 1


async def test_publication_lease_rechecks_current_actor_source_and_job_fence(
    queued, team, mongo_db
):
    queue, _, command, receipt = queued
    _, policy, principals, _, org, kg = team
    job = await queue.claim("first")
    publisher = await queue.acquire_publication(job)
    assert publisher.job_id == job.id
    await expire(mongo_db, job)
    successor = await queue.claim("second")
    with pytest.raises(DomainError):
        await queue.validate_publication(job, publisher)
    # A stale publisher cannot keep the per-KG slot after its job lease expires.
    next_publisher = await queue.acquire_publication(successor)
    assert next_publisher.fence > publisher.fence
    await mongo_db.material_access.update_one(
        {"org_id": str(org), "kg_id": str(command.kg_id), "id": str(receipt.material_id)},
        {"$set": {"tombstoned": True}},
    )
    with pytest.raises(DomainError):
        await queue.validate_publication(successor, next_publisher)
    with pytest.raises(DomainError):
        await queue.get(
            await policy.resolve_access(principals["reader"], [command.kg_id], Action.READ),
            command.id,
        )


async def test_request_cancellation_leaves_job_recoverable_and_runner_resumes(queued, mongo_db):
    from domain_kg.ingestion.pipeline import Pipeline, PipelineStep
    from domain_kg.jobs.runner import JobRunner

    queue, _, _, _ = queued
    started = asyncio.Event()

    async def interrupted(job):
        started.set()
        await asyncio.Event().wait()

    blocked = PipelineStep(stage="parsing", input_hash="1" * 64, execute=interrupted)
    job = await queue.claim("first")
    task = asyncio.create_task(JobRunner(queue, Pipeline((blocked,))).run_job(job))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await mongo_db.jobs.find_one({"id": str(job.id)}))["stage"] != "cancelled"
    await expire(mongo_db, job)
    recovered = await queue.claim("second")
    calls = []

    async def parsed(current):
        calls.append(current.attempt_id)
        return await queue.store_artifact(current, "parsing", "1" * 64, b'{"parsed":true}')

    runner = JobRunner(
        queue, Pipeline((PipelineStep(stage="parsing", input_hash="1" * 64, execute=parsed),))
    )
    await runner.run_job(recovered)
    assert len(calls) == 1
    raw = await mongo_db.jobs.find_one({"id": str(job.id)})
    assert raw["stage"] == "repair_required"  # Parsing cannot pretend publication succeeded.
    await mongo_db.jobs.update_one(
        {"id": str(job.id)}, {"$set": {"available_at": datetime.now(UTC)}}
    )
    await runner.run_job(await queue.claim("third"))
    assert len(calls) == 1


async def test_two_preparers_can_run_but_only_one_can_publish(queued, material_service, mongo_db):
    queue, access, command, _ = queued
    materials, kg = material_service
    await materials.add(
        access,
        AddMaterial(
            kg_id=kg, inline_text="Another source", title="Second", idempotency_key="second-source"
        ),
    )
    first, second = await asyncio.gather(queue.claim("first"), queue.claim("second"))
    assert first and second and first.id != second.id
    lease = await queue.acquire_publication(first)
    with pytest.raises(DomainError) as busy:
        await queue.acquire_publication(second)
    assert busy.value.code == "REVISION_NOT_READY"
    await mongo_db.kgs.update_one(
        {"org_id": str(command.org_id), "id": str(kg)},
        {"$set": {"publication_lease.lease_until": datetime.now(UTC) - timedelta(seconds=1)}},
    )
    successor = await queue.acquire_publication(second)
    assert successor.fence > lease.fence
    with pytest.raises(DomainError):
        await queue.validate_publication(first, lease)
    await queue.validate_publication(second, successor)


async def test_revoked_actor_cannot_activate_an_already_accepted_job(queued, team):
    queue, _, _, _ = queued
    identities, _, _, members, _, _ = team
    job = await queue.claim("worker")
    await identities.change_member(
        members["owner"],
        members["editor"].id,
        expected_revision=members["editor"].revision,
        suspended=True,
        idempotency_key="suspend-worker-actor",
    )
    with pytest.raises(DomainError):
        await queue.authorize(job)
    with pytest.raises(DomainError):
        await queue.acquire_publication(job)


def test_unconfigured_worker_cannot_claim_real_jobs():
    from domain_kg.ingestion.pipeline import Pipeline

    with pytest.raises(DomainError) as pending:
        Pipeline().require_configured()
    assert pending.value.code == "PROVIDER_UNAVAILABLE"


async def test_checkpoint_cannot_reference_a_missing_or_foreign_artifact(queued):
    from domain_kg.jobs.models import Checkpoint

    queue, _, _, _ = queued
    job = await queue.claim("worker")
    missing = Checkpoint(
        org_id=job.org_id,
        kg_id=job.kg_id,
        job_id=job.id,
        stage="parsing",
        artifact_id=uuid4(),
        input_hash="1" * 64,
        output_hash="2" * 64,
    )
    with pytest.raises(DomainError):
        await queue.checkpoint(job, missing)


async def test_checkpoint_resume_rejects_corrupted_stored_bytes(queued):
    import asyncio

    queue, _, _, _ = queued
    job = await queue.claim("worker")
    checkpoint = await queue.store_artifact(job, "parsing", "1" * 64, b'{"parsed":true}')
    await queue.checkpoint(job, checkpoint)
    blob = await queue.artifact_blob(job, checkpoint)
    stores = await queue.router.for_kg(await queue.authorize(job), job.kg_id)
    key = "/".join(
        str(value)
        for value in (blob.org_id, blob.kg_id, blob.material_id, blob.version_id, blob.object_id)
    )
    await asyncio.to_thread(
        stores.blobs.client.put_object,
        Bucket=stores.blobs.bucket,
        Key=key,
        Body=b'{"tampered":true}',
    )
    with pytest.raises(DomainError):
        await queue.load_checkpoint(job, "parsing", "1" * 64)
