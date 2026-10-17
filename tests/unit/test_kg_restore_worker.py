"""KG restore work has a separate provider-aware serial worker mode."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from domain_kg.worker import serve


async def test_kg_restore_worker_claims_only_fresh_kg_restore_jobs():
    queue = SimpleNamespace(claim=AsyncMock(return_value=None))
    runner = SimpleNamespace(pipeline=SimpleNamespace(require_configured=lambda: None))
    await serve(queue, runner, once=True, worker_id="kg-restore", kg_restore_only=True)
    queue.claim.assert_awaited_once_with("kg-restore", kinds=("restore",))
