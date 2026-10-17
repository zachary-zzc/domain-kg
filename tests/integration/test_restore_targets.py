"""Restore work reserves an inactive target without inheriting any access."""

from uuid import uuid4

import pytest

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.jobs.queue import JobQueue
from domain_kg.operations.backup import BackupService
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import BackupCommand, RestoreCommand

pytestmark = pytest.mark.storage


async def test_restore_admission_reserves_fresh_target_without_grants_and_pins_profile(
    material_service, team, catalog, monkeypatch
):
    from domain_kg.operations.restore_targets import RestoreTargets

    materials, kg = material_service
    _, policy, principals, members, org, _ = team
    registry, profile = catalog[1], catalog[3]
    second = profile.model_copy(update={"id": uuid4()})
    operator = await policy.resolve_access(principals["owner"], [], Action.REGISTER_BACKEND)
    await registry.register_profile(operator, second, idempotency_key="restore-second-profile")
    queue = JobQueue(policy, materials.router)
    await queue.initialize()
    operations = OwnerOperations(queue)
    owner = await policy.resolve_access(principals["owner"], [kg], Action.RESTORE)
    backup = await operations.submit(
        owner,
        BackupCommand(
            kg_id=kg, expected_published_revision=0, idempotency_key="restore-empty-backup"
        ),
    )
    job = await queue.claim("backup-for-restore", kinds=("backup",))
    await BackupService(queue).run(job)
    command = RestoreCommand(
        kg_id=kg,
        backup_job_id=backup.job_id,
        expected_published_revision=0,
        placement_id=second.id,
        provider_profile_id=uuid4(),
        name="Inactive recovery",
        idempotency_key="reserve-restore",
    )
    from domain_kg.catalog.models import CreateKG

    other = await catalog[0].create_kg(
        await policy.resolve_access(principals["owner"], [], Action.CREATE_KG),
        CreateKG(
            name="Other namespace",
            domain_profile_id=catalog[0].general_profile_id,
            placement_id=profile.id,
            idempotency_key="restore-other-kg",
        ),
    )
    other_owner = await policy.resolve_access(principals["owner"], [other.kg_id], Action.RESTORE)
    with pytest.raises(DomainError):
        await operations.submit(
            other_owner,
            command.model_copy(
                update={"kg_id": other.kg_id, "idempotency_key": "cross-kg-restore"}
            ),
        )
    owner = await policy.resolve_access(principals["owner"], [kg], Action.RESTORE)
    reader = await policy.resolve_access(principals["reader"], [kg], Action.READ)
    with pytest.raises(DomainError):
        await operations.submit(reader, command)
    # Race a detach after its empty reference read; the registry write fence must
    # force a transaction retry that then observes the new reservation.
    from pymongo.asynchronous.collection import AsyncCollection

    original = AsyncCollection.find_one
    admitted = []

    async def interleaved(collection, query, *args, **kwargs):
        result = await original(collection, query, *args, **kwargs)
        if (
            collection.name == "restore_targets"
            and query == {"placement.id": str(second.id)}
            and not admitted
        ):
            admitted.append(await operations.submit(owner, command))
        return result

    with monkeypatch.context() as patch:
        patch.setattr(AsyncCollection, "find_one", interleaved)
        with pytest.raises(DomainError) as raced:
            await registry.detach_profile(operator, second.id)
        assert raced.value.code == ErrorCode.VERSION_CONFLICT
    assert len(admitted) == 1
    receipt = admitted[0]
    assert receipt.stage == "queued" and await operations.submit(owner, command) == receipt
    target = await RestoreTargets(queue).get(owner, kg, receipt.job_id)
    assert target.target.org_id == org and target.target.kg_id != kg
    assert target.placement == second and target.state == "allocated"
    assert await queue.database.kgs.find_one({"id": str(target.target.kg_id)}) is None
    for table in ("kg_grants", "material_access", "provider_selections"):
        assert await queue.database[table].count_documents({"kg_id": str(target.target.kg_id)}) == 0
    for principal in principals.values():
        with pytest.raises(DomainError):
            await policy.resolve_access(principal, [target.target.kg_id], Action.READ, org_id=org)
    with pytest.raises(DomainError) as pinned:
        await registry.detach_profile(operator, second.id)
    assert pinned.value.code == ErrorCode.VERSION_CONFLICT
    assert await queue.claim("ordinary-source-worker") is None
    leased = await queue.claim("restore-reservation", kinds=("restore",))
    assert leased.id == receipt.job_id
    assert await RestoreTargets(queue).guard(leased) == target
    with pytest.raises(DomainError) as incomplete:
        await queue.finish(leased)
    assert incomplete.value.code == ErrorCode.REVISION_NOT_READY
    assert not await queue.accepted(leased)
    await queue.cancel(owner, receipt.job_id, idempotency_key="cancel-inactive-restore")
    assert (await RestoreTargets(queue).get(owner, kg, receipt.job_id)).state == "abandoned"
    await queue.retry(owner, receipt.job_id, idempotency_key="retry-inactive-restore")
    assert (await RestoreTargets(queue).get(owner, kg, receipt.job_id)).target == target.target
    successor = await queue.claim("restore-recovery", kinds=("restore",))
    assert successor.attempt_id != leased.attempt_id
    await queue.database.storage_profiles.update_one(
        {"id": str(second.id)}, {"$inc": {"revision": 1}}
    )
    with pytest.raises(DomainError) as changed:
        await RestoreTargets(queue).guard(successor)
    assert changed.value.code == ErrorCode.VERSION_CONFLICT
    await queue.database.storage_profiles.update_one(
        {"id": str(second.id)}, {"$inc": {"revision": -1}}
    )
    await queue.database.members.update_one(
        {"org_id": str(org), "id": str(members["owner"].id)}, {"$set": {"suspended": True}}
    )
    with pytest.raises(DomainError):
        await RestoreTargets(queue).guard(successor)


async def test_restore_rejects_missing_or_cross_scope_backup_before_allocating(
    material_service, team, catalog
):
    materials, kg = material_service
    queue = JobQueue(team[1], materials.router)
    await queue.initialize()
    owner = await team[1].resolve_access(team[2]["owner"], [kg], Action.RESTORE)
    command = RestoreCommand(
        kg_id=kg,
        backup_job_id=uuid4(),
        expected_published_revision=0,
        placement_id=catalog[3].id,
        provider_profile_id=uuid4(),
        name="No backup",
        idempotency_key="missing-restore-backup",
    )
    with pytest.raises(DomainError):
        await OwnerOperations(queue).submit(owner, command)
    assert await queue.database.restore_targets.count_documents({"org_id": str(owner.org_id)}) == 0
    assert await queue.database.jobs.count_documents({"kg_id": str(kg), "kind": "restore"}) == 0
