"""Cancellation cannot release a target write record before its actual I/O settles."""

import asyncio
from types import SimpleNamespace

import pytest

from domain_kg.operations.restore_writes import RestoreWrites


class RecordedWrites(RestoreWrites):
    def __init__(self):
        self.events = []

    async def _begin(self, job, target, kind, reference):
        self.events.append("pending")
        return {"id": "test-write"}

    async def _finish(self, key, state):
        self.events.append(state)


async def test_repeated_cancellation_waits_for_the_real_target_write():
    writes, started, release = RecordedWrites(), asyncio.Event(), asyncio.Event()

    async def remote():
        started.set()
        await release.wait()
        writes.events.append("remote-finished")
        return 3

    task = asyncio.create_task(writes.run(SimpleNamespace(), SimpleNamespace(), "graph", remote))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and writes.events == ["pending"]
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert writes.events == ["pending", "remote-finished", "settled"]


async def test_unknown_write_failure_stays_uncertain_without_persisting_exception_text():
    writes = RecordedWrites()

    async def unknown():
        raise RuntimeError("private endpoint and credentials")

    with pytest.raises(RuntimeError):
        await writes.run(SimpleNamespace(), SimpleNamespace(), "vector", unknown)
    assert writes.events == ["pending", "uncertain"]
