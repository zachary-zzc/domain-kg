"""Local cached-image switches for an explicitly opted-in, serial physical drill."""

import asyncio
import importlib.util
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]


async def compose(arguments, *, secondary=False):
    command = ["docker", "compose"]
    if secondary:
        command.extend(["-f", "compose.yaml", "-f", "compose.multi-backend.yaml"])
    return await asyncio.to_thread(
        subprocess.run,
        [*command, *arguments],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )


async def start(*, secondary=False):
    await compose(
        [
            "up",
            "-d",
            "--pull",
            "never",
            "--no-build",
            "--wait",
            "--wait-timeout",
            "120",
            "neo4j",
            "qdrant",
        ],
        secondary=secondary,
    )


async def budget_and_volumes():
    spec = importlib.util.spec_from_file_location("physical_budget", ROOT / "scripts/smoke.py")
    assert spec and spec.loader
    budget = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(budget)
    await asyncio.to_thread(budget.check_memory_budget, running=True)
    identifiers = (await compose(["ps", "--quiet"])).stdout.split()
    template = (
        '{"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
        '"mounts":{{json .Mounts}}}'
    )
    output = await asyncio.to_thread(
        subprocess.check_output,
        ["docker", "inspect", "--format", template, *identifiers],
        cwd=ROOT,
        text=True,
    )
    volumes = {}
    for line in output.splitlines():
        row = json.loads(line)
        if row["service"] in ("neo4j", "qdrant"):
            destination = "/data" if row["service"] == "neo4j" else "/qdrant/storage"
            volumes[row["service"]] = next(
                mount["Name"] for mount in row["mounts"] if mount["Destination"] == destination
            )
    assert set(volumes) == {"neo4j", "qdrant"}
    return volumes
