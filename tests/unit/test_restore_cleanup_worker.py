"""Cleanup is an explicit serial worker role and never starts a model."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from domain_kg.worker import serve


async def test_restore_cleanup_worker_claims_only_inactive_restore_cleanup():
    queue = SimpleNamespace(claim=AsyncMock(return_value=None))
    runner = SimpleNamespace(pipeline=SimpleNamespace(require_configured=lambda: None))
    await serve(queue, runner, once=True, worker_id="cleanup", restore_cleanup_only=True)
    queue.claim.assert_awaited_once_with("cleanup", kinds=("restore_cleanup",))
