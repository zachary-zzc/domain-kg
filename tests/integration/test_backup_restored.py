"""Restored accepted attempts remain independently verifiable by a later backup."""
# ruff: noqa: F811

import pytest
from test_publication import publishing  # noqa: F401
from test_restore_preparation import setup_restore

from domain_kg.identity.models import Action
from domain_kg.operations.backup import BackupService
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import BackupCommand
from domain_kg.operations.restore_activation import RestoreActivation
from domain_kg.operations.restore_authority import RestoreAuthority
from domain_kg.operations.restore_control import RestoreControl
from domain_kg.operations.restore_preparation import RestorePreparation
from domain_kg.operations.restore_projections import RestoreProjections

pytestmark = pytest.mark.storage


async def test_restored_native_kg_can_be_backed_up_without_old_active_leases(
    publishing, team, catalog
):
    _, _, job, target, _ = await setup_restore(publishing, team, catalog)
    _, _, queue, coordinator, _, _ = publishing
    staged = await RestorePreparation(queue, coordinator.providers).prepare(job)
    try:
        await RestoreAuthority(queue, coordinator.providers).stage(job, staged)
        await RestoreControl(queue, coordinator.providers).stage(job, staged)
        parts = await RestoreProjections(queue, coordinator.providers).stage(job, staged)
        await RestoreActivation(queue, coordinator.providers).activate(job, staged, parts)
        owner = await queue.policy.resolve_access(
            team[2]["owner"], [target.target.kg_id], Action.BACKUP
        )
        receipt = await OwnerOperations(queue).submit(
            owner,
            BackupCommand(
                kg_id=target.target.kg_id,
                expected_published_revision=1,
                idempotency_key="backup-restored-target",
            ),
        )
        backup_job = await queue.claim("backup-restored", kinds=("backup",))
        assert backup_job.id == receipt.job_id
        await BackupService(queue).run(backup_job)
        manifest = await BackupService(queue).manifest(owner, target.target.kg_id, receipt.job_id)
        assert manifest.source_count == 1
        assert (
            await BackupService(queue).verify(owner, target.target.kg_id, manifest.backup_id)
            == manifest
        )
    finally:
        await staged.binding.stores.vectors.client.delete_collection(
            staged.binding.stores.vectors.collection
        )
        await staged.binding.stores.graph.driver.execute_query(
            "MATCH (n {org_id:$org,kg_id:$kg}) DETACH DELETE n",
            org=str(target.target.org_id),
            kg=str(target.target.kg_id),
        )
