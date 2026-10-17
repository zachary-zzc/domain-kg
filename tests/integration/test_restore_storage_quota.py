"""Original restore budgets are reserved transactionally before target copying."""
# ruff: noqa: F811

import asyncio

import pytest
from test_publication import publishing  # noqa: F401
from test_restore_preparation import setup_restore

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.models import Namespace
from domain_kg.operations.backup import BackupService
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import RestoreCommand
from domain_kg.operations.package_reader import PackageReader
from domain_kg.operations.restore_inventory import RestoreInventory
from domain_kg.operations.restore_preparation import RestorePreparation
from domain_kg.operations.restore_quota import RestoreQuota, RestoreQuotaReservation, original_bytes
from domain_kg.operations.restore_stores import RestoreStores
from domain_kg.operations.restore_targets import RestoreTargets

pytestmark = pytest.mark.storage


async def test_restore_quota_rejection_precedes_any_target_provisioning(
    publishing, team, catalog, monkeypatch
):
    _, _, job, target, _ = await setup_restore(publishing, team, catalog)
    _, _, queue, coordinator, _, _ = publishing
    quota = await queue.database.org_quotas.find_one({"org_id": str(job.org_id)})
    await queue.database.org_quotas.update_one(
        {"org_id": str(job.org_id)}, {"$set": {"limit_bytes": quota["committed_bytes"]}}
    )

    async def forbidden(*args, **kwargs):
        pytest.fail("over-quota restore must stop before target provisioning or writes")

    monkeypatch.setattr(RestoreStores, "provision", forbidden)
    with pytest.raises(DomainError) as failure:
        await RestorePreparation(queue, coordinator.providers).prepare(job)
    assert failure.value.code == ErrorCode.QUOTA_EXCEEDED
    assert await queue.database.kgs.find_one({"id": str(target.target.kg_id)}) is None
    assert (
        await queue.database.restore_quota_reservations.count_documents({"org_id": str(job.org_id)})
        == 0
    )


async def test_concurrent_restore_reservations_share_org_limit_and_replay_once(
    publishing, team, catalog
):
    source, owner, first, first_target, backup = await setup_restore(publishing, team, catalog)
    _, _, queue, coordinator, _, _ = publishing
    profile = next(iter(coordinator.providers.registry.profiles.values()))
    receipt = await OwnerOperations(queue).submit(
        owner,
        RestoreCommand(
            kg_id=source.kg_id,
            expected_published_revision=1,
            backup_job_id=backup.job_id,
            placement_id=catalog[3].id,
            provider_profile_id=profile.id,
            name="Second inactive recovery",
            idempotency_key="second-restore-quota",
        ),
    )
    second = await queue.claim("second-restore-quota", kinds=("restore",))
    second_target = await RestoreTargets(queue).get(owner, source.kg_id, receipt.job_id)
    _, manifest, store = await BackupService(queue)._manifest(
        owner, source.kg_id, backup.job_id, by_job=True
    )
    inventory = await RestoreInventory.load(
        PackageReader(store, lambda: RestoreTargets(queue).guard(first)), manifest
    )
    amount = original_bytes(inventory.parts, Namespace(org_id=source.org_id, kg_id=source.kg_id))
    quota = await queue.database.org_quotas.find_one({"org_id": str(source.org_id)})
    await queue.database.org_quotas.update_one(
        {"org_id": str(source.org_id)}, {"$set": {"limit_bytes": quota["committed_bytes"] + amount}}
    )
    service = RestoreQuota(queue)
    jobs = ((first, first_target), (second, second_target))
    results = await asyncio.gather(
        *(service.reserve(job, target, manifest, inventory.parts) for job, target in jobs),
        return_exceptions=True,
    )
    assert sum(isinstance(result, RestoreQuotaReservation) for result in results) == 1
    assert (
        sum(
            isinstance(result, DomainError) and result.code == ErrorCode.QUOTA_EXCEEDED
            for result in results
        )
        == 1
    )
    winner = next(
        index for index, result in enumerate(results) if isinstance(result, RestoreQuotaReservation)
    )
    job, target = jobs[winner]
    await service.reserve(job, target, manifest, inventory.parts)
    saved = await queue.database.org_quotas.find_one({"org_id": str(source.org_id)})
    assert (
        saved["reserved_bytes"] == amount and saved["committed_bytes"] == quota["committed_bytes"]
    )
    target_quota = await queue.database.kg_quotas.find_one(
        {"org_id": str(source.org_id), "kg_id": str(target.target.kg_id)}
    )
    assert target_quota["reserved_bytes"] == amount and target_quota["committed_bytes"] == 0
