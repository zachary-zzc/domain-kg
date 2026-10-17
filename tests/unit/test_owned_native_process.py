"""The native watchdog terminates only its owned child, including error exits."""

import asyncio
import sys

import pytest

from domain_kg.errors import DomainError


async def test_exception_stops_owned_native_process_without_touching_another_child(
    tmp_path, monkeypatch
):
    from domain_kg.providers import native_process

    async def measured(_):
        return 1024

    monkeypatch.setattr(native_process, "rss_bytes", measured)
    other = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time;time.sleep(20)")
    guard = native_process.OwnedProcess(
        [sys.executable, "-c", "import time;time.sleep(20)"],
        tmp_path / "native.log",
        rss_cap_bytes=1000000,
        sample_seconds=0.05,
    )
    try:
        with pytest.raises(RuntimeError, match="caller failed"):
            async with guard:
                await guard.require_alive()
                raise RuntimeError("caller failed")
        assert guard.process.returncode is not None
        assert other.returncode is None
    finally:
        other.terminate()
        await other.wait()


async def test_memory_limit_stops_owned_process_and_fails_the_operation(tmp_path, monkeypatch):
    from domain_kg.providers import native_process

    async def measured(_):
        return 2000000

    monkeypatch.setattr(native_process, "rss_bytes", measured)
    guard = native_process.OwnedProcess(
        [sys.executable, "-c", "import time;time.sleep(20)"],
        tmp_path / "native.log",
        rss_cap_bytes=1000000,
        sample_seconds=0.02,
    )
    with pytest.raises(DomainError):
        async with guard:
            await asyncio.sleep(0.1)
            await guard.require_alive()
    assert guard.exceeded and guard.peak_rss_bytes == 2000000
    assert guard.process.returncode is not None
