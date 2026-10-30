"""Start the bounded local stores and run the complete Alibaba MCP test."""

import argparse
import os
import signal
import subprocess
from pathlib import Path

from scripts.configure_alibaba import credential
from scripts.preview import run_owned
from scripts.smoke import ROOT, SERVICES, check_docker_space, check_memory_budget


def execute(*, live: bool, keep_services: bool = False) -> None:
    python = ROOT / ".venv/bin/python"
    if not python.is_file():
        raise RuntimeError("Run uv sync --locked once to prepare the pinned environment.")
    env = dict(os.environ)
    env["PYTEST_ADDOPTS"] = ""
    env.pop("DOMAIN_KG_BROWSER_PREVIEW", None)
    # Ambient environment must never turn the default check into a paid API test.
    env["DOMAIN_KG_ALIBABA_LIVE"] = "1" if live else "0"
    if live:
        key_path = Path(
            env.get("DOMAIN_KG_ALIBABA_KEY_FILE", str(ROOT / "secrets/dashscope-api-key"))
        ).expanduser().resolve()
        credential(key_path)
        env["DOMAIN_KG_ALIBABA_KEY_FILE"] = str(key_path)
    check_docker_space()
    check_memory_budget()
    previous = set(
        subprocess.check_output(
            ["docker", "compose", "ps", "--services", "--status", "running"],
            cwd=ROOT,
            text=True,
        ).split()
    )
    started = [service for service in SERVICES if service not in previous]

    def run(arguments: list[str], timeout: int = 180) -> None:
        print("+ " + " ".join(arguments), flush=True)
        run_owned(arguments, env, timeout=timeout)

    print(
        "Live Alibaba test: a tiny public fixture, with bounded API calls."
        if live else "Local MCP test: real databases, mocked model responses, zero API calls.",
        flush=True,
    )
    try:
        run(["docker", "compose", "up", "-d", "--wait", "--wait-timeout", "120", *SERVICES])
        run([str(python), "scripts/init_replica_set.py"])
        check_memory_budget(running=True)
        run(
            [str(python), "-m", "pytest", "tests/evaluation/test_alibaba_mcp.py", "-q", "-s"],
            timeout=600,
        )
    finally:
        if started and not keep_services:
            run(["docker", "compose", "stop", *started])
    print("Passed: owner setup, ingestion, publication, fast/deep search and access checks.")
    if live:
        print(f"Report: {ROOT / '.cache/alibaba-35b/live-mcp-smoke.json'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use the saved Alibaba key (paid API)")
    parser.add_argument("--keep-services", action="store_true", help="Leave started databases up")
    args = parser.parse_args()
    os.chdir(ROOT)

    def interrupted(*_: object) -> None:
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        execute(live=args.live, keep_services=args.keep_services)
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    main()
