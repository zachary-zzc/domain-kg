"""The single laptop owner worker must never claim model-backed source jobs."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from domain_kg.worker import serve


async def test_owner_worker_claims_only_backup_and_export():
    queue = SimpleNamespace(claim=AsyncMock(return_value=None))
    runner = SimpleNamespace(pipeline=SimpleNamespace(require_configured=lambda: None))
    await serve(queue, runner, once=True, worker_id="owner-only", operations_only=True)
    queue.claim.assert_awaited_once_with("owner-only", kinds=("backup", "export"))
