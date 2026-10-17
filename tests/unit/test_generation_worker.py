"""Rebuild execution is explicitly selected, serial and model-free."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from domain_kg.worker import serve


async def test_generation_worker_claims_only_its_configured_kind():
    queue = SimpleNamespace(claim=AsyncMock(return_value=None))
    runner = SimpleNamespace(pipeline=SimpleNamespace(require_configured=lambda: None))
    await serve(queue, runner, once=True, worker_id="generation", generation_only=True)
    queue.claim.assert_awaited_once_with("generation", kinds=("generation",))
