import asyncio

import httpx
import pytest

from eval.scale_guard import Runner, pool


async def test_billing_failure_stops_new_calls_without_retry(tmp_path):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            400, json={"error": {"code": "Arrearage", "message": "account overdue"}}
        )

    runner = Runner(tmp_path, "private-key", interval=0)
    payload = {"model": "qwen3.5-397b-a17b", "max_tokens": 100, "messages": []}
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(RuntimeError, match="Arrearage"):
            await runner.call(client, "first", payload)
        with pytest.raises(RuntimeError, match="Arrearage"):
            await runner.call(client, "next", payload)
    assert len(calls) == 1
    assert len(list(tmp_path.glob("*.result.json"))) == 1
    assert len(list(tmp_path.glob("*.launch.json"))) == 1


async def test_worker_failure_drains_inflight_calls_before_exit():
    started, finished = asyncio.Event(), asyncio.Event()

    async def worker(job):
        if job == "fail":
            await started.wait()
            raise RuntimeError("stop new calls")
        started.set()
        await asyncio.sleep(0.02)
        finished.set()

    with pytest.raises(RuntimeError, match="stop new calls"):
        await pool(["fail", "inflight"], worker, 2)
    assert finished.is_set()
