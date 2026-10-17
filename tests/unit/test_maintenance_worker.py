"""Curation maintenance is a dedicated serial, model-free worker mode."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from domain_kg.worker import serve


async def test_maintenance_worker_claims_only_owner_maintenance():
    queue = SimpleNamespace(claim=AsyncMock(return_value=None))
    runner = SimpleNamespace(pipeline=SimpleNamespace(require_configured=lambda: None))
    await serve(queue, runner, once=True, worker_id="curation-repair", maintenance_only=True)
    queue.claim.assert_awaited_once_with("curation-repair", kinds=("maintenance",))
