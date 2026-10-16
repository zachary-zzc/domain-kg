"""Tiny public multilingual CPU smoke for the approved checksum-pinned model.

Starts one owned native process, measures RSS, checks real provider-client vectors,
and stops it afterwards. Docker and other processes are never modified.
"""

import asyncio
import hashlib
import json
import math
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from uuid import uuid4

import httpx
from pydantic import SecretStr

from domain_kg.models import Namespace
from domain_kg.providers.embeddings import EmbeddingClient, EmbeddingProfile
from domain_kg.providers.profiles import ProviderConnection, ProviderProfile, embedding_space_id

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path.home() / ".cache/domain-kg"
MODEL = CACHE / "models/Qwen3-Embedding-0.6B-Q8_0.gguf"
SHA256 = "06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439"
LIMIT = 1_250_000_000


async def main() -> None:
    with MODEL.open("rb") as source:
        assert hashlib.file_digest(source, "sha256").hexdigest() == SHA256
    assert MODEL.stat().st_size == 639150592
    with socket.socket() as check:
        if check.connect_ex(("127.0.0.1", 29500)) == 0:
            raise RuntimeError("Pause the chat model first; this smoke runs sequentially.")
    with socket.socket() as check:
        if check.connect_ex(("127.0.0.1", 29501)) == 0:
            raise RuntimeError("Port 29501 is already in use; no process was changed.")
    output = CACHE / "embedding-smoke"
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_path = output / "server.log"
    peak = 0
    exceeded = threading.Event()
    with log_path.open("ab") as log:
        os.chmod(log_path, 0o600)
        process = await asyncio.to_thread(
            subprocess.Popen,
            [
                "/usr/bin/nice",
                "-n",
                "10",
                "/opt/homebrew/bin/llama-server",
                "--model",
                str(MODEL),
                "--alias",
                "domain-kg-small-embedding",
                "--host",
                "127.0.0.1",
                "--port",
                "29501",
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
                "2048",
                "--batch-size",
                "2048",
                "--ubatch-size",
                "2048",
                "--embedding",
                "--pooling",
                "last",
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
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

        def watch() -> None:
            nonlocal peak
            while process.poll() is None:
                measured = subprocess.run(
                    ["/bin/ps", "-o", "rss=", "-p", str(process.pid)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if measured.returncode == 0 and measured.stdout.strip():
                    peak = max(peak, int(measured.stdout.strip()) * 1024)
                    if peak > LIMIT:
                        exceeded.set()
                        process.terminate()
                        return
                time.sleep(0.25)

        threading.Thread(target=watch, daemon=True).start()
        report: dict[str, object] = {
            "sha256": SHA256,
            "context_tokens": 2048,
            "threads": 2,
            "dimensions": 1024,
            "status": "failed",
        }
        try:
            token = (CACHE / "llm-api-key").read_text().strip()
            async with httpx.AsyncClient(trust_env=False, timeout=2) as http:
                deadline = time.monotonic() + 90
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("Embedding server exited during startup.")
                    try:
                        if (await http.get("http://127.0.0.1:29501/health")).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Embedding startup deadline exceeded.")
                    await asyncio.sleep(0.5)
            namespace = Namespace(org_id=uuid4(), kg_id=uuid4())
            approved = ProviderProfile(
                id=uuid4(),
                connection_ref="local",
                extraction_model="unused",
                embedding_model="domain-kg-small-embedding",
                embedding_dimensions=1024,
                allowed_namespaces=(namespace,),
                data_policy_approved=True,
                embedding_context_tokens=2048,
                embedding_batch_size=1,
                embedding_query_prefix="Instruct: Retrieve relevant evidence passages.\nQuery: ",
            )
            connection = ProviderConnection(
                ref="local", endpoint="http://127.0.0.1:29501/v1", api_key=SecretStr(token)
            )
            profile = EmbeddingProfile(
                **namespace.model_dump(),
                id=approved.id,
                revision=1,
                model=approved.embedding_model,
                dimensions=1024,
                space_id=embedding_space_id(approved, connection),
            )
            usage = []

            async def authorize() -> None:
                if process.poll() is not None or exceeded.is_set():
                    raise RuntimeError("Owned embedding process unavailable.")

            async def record(value: object) -> None:
                usage.append(value)

            client = EmbeddingClient(approved, connection, authorize=authorize, record_usage=record)
            started = time.monotonic()
            try:
                docs = await client.embed(
                    [
                        "A cat is sitting on a mat.",
                        "A spaceship explores distant galaxies.",
                        "一只猫坐在垫子上。",
                        "飞船探索遥远的星系。",
                    ],
                    profile,
                )
                queries = await client.embed(
                    ["Where is the cat?", "飞船在探索哪里？"], profile, query=True
                )
            finally:
                await client.close()
            assert all(
                len(vector) == 1024 and abs(math.hypot(*vector) - 1) < 1e-6
                for vector in (*docs, *queries)
            )
            similarities = [
                [sum(a * b for a, b in zip(query, doc, strict=True)) for doc in docs]
                for query in queries
            ]
            assert similarities[0][0] > similarities[0][1]
            assert similarities[0][2] > similarities[0][3]
            assert similarities[1][1] > similarities[1][0]
            assert similarities[1][3] > similarities[1][2]
            assert not exceeded.is_set()
            report.update(
                status="passed",
                seconds=round(time.monotonic() - started, 3),
                calls=len(usage),
                similarities=similarities,
            )
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            report.update(
                peak_rss_bytes=peak,
                rss_watchdog_bytes=LIMIT,
                limit_exceeded=exceeded.is_set(),
                server_stopped=True,
            )
            (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
