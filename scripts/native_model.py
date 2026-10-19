"""Explicit, pinned native models for the real PDF runner. No downloads/autostart."""

import asyncio
import hashlib
import json
import socket
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from domain_kg.providers.native_process import OwnedProcess

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path.home() / ".cache/domain-kg"


def embedding_window(limits, requested):
    if requested is None:
        return limits
    if type(requested) is not int or not 128 <= requested <= min(limits):
        raise ValueError("Embedding window may only reduce approved allocation")
    return requested, requested, requested


@asynccontextmanager
async def model(
    role, *, manifest_path=ROOT / "config/medical-model.approved.json", log_directory=None
):
    settings = json.loads(manifest_path.read_bytes())
    laptop = json.loads((ROOT / settings["docker_cap_profile"]).read_bytes())["native"]
    if role == "chat":
        selected, port, cap = settings, 29500, settings["rss_watchdog_bytes"]
        context, batch, micro = (
            settings[k] for k in ("context_tokens", "batch_tokens", "microbatch_tokens")
        )
        extra = [
            "--reasoning-budget",
            "0",
            "--reasoning",
            "off",
            "--chat-template-kwargs",
            '{"enable_thinking":false}',
        ]
    elif role == "embedding":
        selected = json.loads((ROOT / settings["embedding_manifest"]).read_bytes())["embeddings"]
        port, cap = 29501, laptop["rss_cap_per_process_bytes"]
        context, batch, micro = (
            laptop[k]
            for k in (
                "embedding_context_tokens",
                "embedding_batch_tokens",
                "embedding_microbatch_tokens",
            )
        )
        context, batch, micro = embedding_window(
            (context, batch, micro), settings.get("embedding_window_tokens")
        )
        extra = ["--embedding", "--pooling", "last"]
    else:
        raise ValueError("Unknown explicit model role.")
    assert settings["threads"] <= 2 and settings["parallel_slots"] == 1
    assert context <= 4096 and cap <= (3500000000 if role == "chat" else 1250000000)
    for check_port in (29500, 29501):
        with socket.socket() as check:
            if check.connect_ex(("127.0.0.1", check_port)) == 0:
                raise RuntimeError("Model port already occupied; no existing process changed.")
    path = CACHE / "models" / selected["filename"]
    with path.open("rb") as stream:
        actual = await asyncio.to_thread(hashlib.file_digest, stream, "sha256")
    if path.stat().st_size != selected["size_bytes"] or actual.hexdigest() != selected["sha256"]:
        raise RuntimeError("Pinned native model differs; nothing was started.")
    command = [
        "/usr/bin/nice",
        "-n",
        "10",
        settings["runtime"],
        "--model",
        str(path),
        "--alias",
        selected["served_model_name"],
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--device",
        "none",
        "--gpu-layers",
        "0",
        "--threads",
        str(settings["threads"]),
        "--threads-batch",
        str(settings["threads"]),
        "--threads-http",
        "2",
        "--parallel",
        "1",
        "--ctx-size",
        str(context),
        "--batch-size",
        str(batch),
        "--ubatch-size",
        str(micro),
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
        *extra,
    ]
    guard = OwnedProcess(
        command,
        (log_directory or CACHE / "real-medical-kg") / f"{role}-server.log",
        rss_cap_bytes=cap,
        sample_seconds=0.25,
    )
    async with guard:
        async with httpx.AsyncClient(trust_env=False, timeout=2) as http:
            deadline = asyncio.get_running_loop().time() + laptop["startup_timeout_seconds"]
            while True:
                await guard.require_alive()
                try:
                    if (await http.get(f"http://127.0.0.1:{port}/health")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if asyncio.get_running_loop().time() > deadline:
                    raise RuntimeError("Native model startup timed out.")
                await asyncio.sleep(0.5)
        yield guard
        await guard.require_alive()
