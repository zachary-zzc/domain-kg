"""Separate explicit 7B resource profile; leaves the approved 4B guard intact."""

import asyncio
import hashlib
import json
import socket
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from domain_kg.providers.native_process import OwnedProcess
from scripts.native_model import CACHE


def verified_paths(settings, directory):
    paths = []
    for shard in settings["shards"]:
        name = shard["filename"]
        if Path(name).name != name:
            raise ValueError("Shard path escapes model cache")
        path = directory / name
        if not path.is_file() or path.stat().st_size != shard["size_bytes"]:
            raise ValueError("Missing or incorrect pinned shard size")
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != shard["sha256"]:
            raise ValueError("Pinned shard hash differs")
        paths.append(path)
    if len(paths) != 2 or len(set(paths)) != 2:
        raise ValueError("Exactly two distinct pinned model shards are required")
    return paths


@asynccontextmanager
async def seven_b_model(manifest_path, output):
    settings = json.loads(manifest_path.read_bytes())
    if (
        settings["threads"] != 2
        or settings["parallel_slots"] != 1
        or settings["gpu_layers"] != 0
        or settings["rss_watchdog_bytes"] > 6000000000
        or settings["context_tokens"] > 4096
        or settings["endpoint"] != "http://127.0.0.1:29500/v1"
    ):
        raise ValueError("Invalid isolated 7B experiment limits")
    for port in (29500, 29501):
        with socket.socket() as check:
            if check.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError("Model port occupied; no existing process changed")
    directory = CACHE / "models" / settings["cache_subdirectory"]
    paths = await asyncio.to_thread(verified_paths, settings, directory)
    command = [
        "/usr/bin/nice",
        "-n",
        "10",
        settings["runtime"],
        "--model",
        str(paths[0]),
        "--alias",
        settings["served_model_name"],
        "--host",
        "127.0.0.1",
        "--port",
        "29500",
        "--device",
        "none",
        "--gpu-layers",
        "0",
        "--threads",
        "2",
        "--threads-batch",
        "2",
        "--threads-http",
        "2",
        "--parallel",
        "1",
        "--ctx-size",
        "4096",
        "--batch-size",
        "128",
        "--ubatch-size",
        "128",
        "--cache-ram",
        "0",
        "--ctx-checkpoints",
        "0",
        "--poll",
        "0",
        "--poll-batch",
        "0",
        "--no-mmproj",
        "--no-ui",
        "--no-slots",
        "--no-repack",
        "--no-warmup",
        "--api-key-file",
        str(CACHE / "llm-api-key"),
    ]
    async with OwnedProcess(
        command, output / "7b-server.log", rss_cap_bytes=settings["rss_watchdog_bytes"]
    ) as guard:
        async with httpx.AsyncClient(trust_env=False, timeout=2) as http:
            deadline = asyncio.get_running_loop().time() + 120
            while True:
                await guard.require_alive()
                try:
                    if (await http.get("http://127.0.0.1:29500/health")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if asyncio.get_running_loop().time() > deadline:
                    raise RuntimeError("7B startup timed out")
                await asyncio.sleep(0.5)
        try:
            yield guard
        finally:
            (output / "runtime-usage.json").write_text(
                json.dumps(
                    {
                        "pid": guard.process.pid,
                        "peak_rss_bytes": guard.peak_rss_bytes,
                        "cap_bytes": guard.cap,
                        "exceeded": guard.exceeded,
                        "monitor_failed": guard.monitor_failed,
                    },
                    indent=2,
                )
            )
