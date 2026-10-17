"""A failed inactive target is erased before quota release; its live source survives."""
# ruff: noqa: F811

import asyncio

import pytest
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_publication import publishing  # noqa: F401
from test_restore_preparation import setup_restore

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.restore import RestoreService
from domain_kg.operations.restore_activation import RestoreActivation
from domain_kg.operations.restore_cleanup import RestoreCleanupService

pytestmark = pytest.mark.storage


async def test_failed_restore_cleanup_is_scoped_resumable_and_refunds_once(
    publishing, team, catalog, monkeypatch, issuer_fixture
):
    from domain_kg.operations.models import RestoreCleanupCommand

    source, owner, restore, target, _ = await setup_restore(publishing, team, catalog)
    _, _, queue, coordinator, _, _ = publishing

    async def stop_before_activation(*args, **kwargs):
        raise DomainError(ErrorCode.VALIDATION_ERROR, "Synthetic preactivation failure.")

    monkeypatch.setattr(RestoreActivation, "activate", stop_before_activation)
    await JobRunner(
        queue,
        configured_pipeline(queue, coordinator.providers),
        operations=RestoreService(queue, coordinator.providers),
    ).run_job(restore)
    assert (await queue.get(owner, restore.id)).stage == "failed"
    target_ns = target.target.model_dump(mode="json")
    quota = await queue.database.kg_quotas.find_one(target_ns)
    assert quota["reserved_bytes"] > 0 and quota["committed_bytes"] == 0
    stores = await queue.router._bind_namespace(target.target, target.placement, target.generation)
    source_stores = await queue.router.for_kg(owner, source.kg_id)
    source_points = (
        await source_stores.vectors.client.count(source_stores.vectors.collection, exact=True)
    ).count
    assert (await stores.vectors.client.count(stores.vectors.collection, exact=True)).count > 0
    unknown_key = f"{target.target.org_id}/{target.target.kg_id}/unrecorded"
    await asyncio.to_thread(
        stores.blobs.client.put_object,
        Bucket=stores.blobs.bucket,
        Key=unknown_key,
        Body=b"must block quota release",
    )
    command = RestoreCleanupCommand(
        kg_id=source.kg_id,
        restore_job_id=restore.id,
        expected_published_revision=1,
        idempotency_key="cleanup-failed-restore",
    )
    operations = OwnerOperations(queue)
    for role in ("reader", "editor"):
        from domain_kg.identity.models import Action

        denied = await queue.policy.resolve_access(team[2][role], [source.kg_id], Action.READ)
        with pytest.raises(DomainError):
            await operations.submit(denied, command)
    from restore_cleanup_browser import submit_cleanup_via_browser

    receipt = await submit_cleanup_via_browser(
        command, team, catalog, coordinator.providers, issuer_fixture
    )
    assert await operations.submit(owner, command) == receipt
    with pytest.raises(DomainError):
        await queue.retry(owner, restore.id, idempotency_key="must-not-restart-discarded-target")
    assert await queue.claim("ordinary-worker") is None
    cleanup = await queue.claim("cleanup", kinds=("restore_cleanup",))
    assert cleanup.id == receipt.job_id
    service = RestoreCleanupService(queue)
    runner = JobRunner(queue, configured_pipeline(queue, coordinator.providers), operations=service)
    await runner.run_job(cleanup)
    assert (await queue.get(owner, cleanup.id)).stage == "failed"
    assert (await queue.database.kg_quotas.find_one(target_ns))["reserved_bytes"] == quota[
        "reserved_bytes"
    ]
    assert not await queue.database.restore_cleanup_results.find_one({"id": str(cleanup.target_id)})
    await asyncio.to_thread(
        stores.blobs.client.delete_object, Bucket=stores.blobs.bucket, Key=unknown_key
    )
    from domain_kg.operations.audit import AuditTrail

    append = AuditTrail.append

    async def fail_completion(self, access, kg, operation, identifier, kind, outcome, session):
        if kind == "restore_cleanup" and outcome == "completed":
            raise RuntimeError("Synthetic cleanup commit interruption.")
        return await append(self, access, kg, operation, identifier, kind, outcome, session)

    monkeypatch.setattr(AuditTrail, "append", fail_completion)
    await queue.retry(owner, cleanup.id, idempotency_key="cleanup-commit-interruption")
    interrupted = await queue.claim("cleanup-interrupted", kinds=("restore_cleanup",))
    await runner.run_job(interrupted)
    assert (await queue.database.kg_quotas.find_one(target_ns))["reserved_bytes"] == quota[
        "reserved_bytes"
    ]
    assert not await queue.database.restore_cleanup_results.find_one({"id": str(cleanup.target_id)})
    assert await stores.authority._database.materials.find_one(target_ns)
    monkeypatch.setattr(AuditTrail, "append", append)
    assert (await queue.get(owner, cleanup.id)).stage == "queued"
    await asyncio.sleep(1.05)  # Existing bounded automatic-retry backoff.
    retry = await queue.claim("cleanup-retry", kinds=("restore_cleanup",))
    assert retry.id == cleanup.id and retry.attempt_id != cleanup.attempt_id
    await runner.run_job(retry)
    assert (await queue.get(owner, retry.id)).stage == "ready"
    assert await queue.accepted(retry)
    await queue.finish(retry)
    await service.run(retry)
    final = await queue.database.kg_quotas.find_one(target_ns)
    assert final["reserved_bytes"] == final["committed_bytes"] == 0
    assert (await stores.vectors.client.count(stores.vectors.collection, exact=True)).count == 0
    rows, _, _ = await stores.graph.driver.execute_query(
        "MATCH (n {org_id:$org,kg_id:$kg}) RETURN count(n) AS total",
        org=target_ns["org_id"],
        kg=target_ns["kg_id"],
    )
    assert rows[0]["total"] == 0
    assert (
        await source_stores.vectors.client.count(source_stores.vectors.collection, exact=True)
    ).count == source_points
    assert not await queue.database.kgs.find_one(
        {"org_id": target_ns["org_id"], "id": target_ns["kg_id"]}
    )
    assert not await stores.authority._database.materials.find_one(target_ns)
    assert not await queue.database.material_access.find_one(target_ns)
    assert (await queue.database.kgs.find_one({"id": str(source.kg_id)}))["published_revision"] == 1
    assert (
        await queue.database.operation_audit.count_documents(
            {"job_id": str(retry.id), "outcome": "completed"}
        )
        == 1
    )
