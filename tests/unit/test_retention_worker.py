"""Retention claims only explicit cleanup jobs, without starting a model."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from domain_kg.worker import serve


async def test_retention_worker_is_separate_from_source_and_backup_workers():
    queue = SimpleNamespace(claim=AsyncMock(return_value=None))
    runner = SimpleNamespace(pipeline=SimpleNamespace(require_configured=lambda: None))
    await serve(queue, runner, once=True, worker_id="retention", retention_only=True)
    queue.claim.assert_awaited_once_with("retention", kinds=("retention",))
