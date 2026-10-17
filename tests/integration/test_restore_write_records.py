"""Actual leased target writes register intent and reject cancellation before remote I/O."""
# ruff: noqa: F811

from unittest.mock import AsyncMock

import pytest
from test_publication import publishing  # noqa: F401
from test_restore_preparation import setup_restore

from domain_kg.errors import DomainError
from domain_kg.operations.restore_writes import RestoreWrites

pytestmark = pytest.mark.storage


async def test_restore_target_write_records_are_scoped_and_drained(publishing, team, catalog):
    source, owner, job, target, _ = await setup_restore(publishing, team, catalog)
    queue = publishing[2]
    writes = RestoreWrites(queue)
    remote = AsyncMock(return_value=7)
    assert await writes.run(job, target, "graph", remote) == 7
    key = {"org_id": str(source.org_id), "kg_id": str(source.kg_id), "job_id": str(job.id)}
    records = await queue.database.restore_writes.find(key).to_list()
    assert len(records) == 1 and records[0]["state"] == "settled"
    assert records[0]["target"] == target.target.model_dump(mode="json")
    assert records[0]["attempt_id"] == str(job.attempt_id)

    async def unknown():
        raise RuntimeError("private payload must not be saved")

    with pytest.raises(RuntimeError):
        await writes.run(job, target, "vector", unknown)
    pending = await queue.database.restore_writes.find_one(key | {"state": "uncertain"})
    assert pending is not None and "private payload" not in str(pending)
    from domain_kg.operations.jobs import OwnerOperations
    from domain_kg.operations.restore_management import RestoreManagement

    info = await RestoreManagement(OwnerOperations(queue), publishing[3].providers.registry).get(
        owner, source.kg_id, job.id
    )
    assert info.write_tracking and info.pending_writes == 0 and info.uncertain_writes == 1
    await queue.cancel(owner, job.id, idempotency_key="cancel-recorded-writer")
    remote.reset_mock()
    with pytest.raises(DomainError):
        await writes.run(job, target, "graph", remote)
    remote.assert_not_called()
