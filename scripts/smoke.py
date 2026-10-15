"""Run small laptop checks; never prune Docker data or resize its VM."""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ["mongo", "neo4j", "qdrant", "object-store"]
TASK_FILES = {
    1: [
        "tests/protocol/test_transport_smoke.py",
        "tests/integration/test_storage_smoke.py",
        "tests/integration/test_lightrag_scope_probe.py",
    ],
}


def run(args: list[str], env: dict[str, str]) -> None:
    print("+ " + " ".join(args), flush=True)
    subprocess.run(args, cwd=ROOT, env=env, check=True)


def check_docker_space() -> None:
    subprocess.run(["docker", "info"], check=True, capture_output=True, text=True)
    context = subprocess.check_output(["docker", "context", "show"], text=True).strip()
    if context.startswith("colima") and shutil.which("colima"):
        output = subprocess.check_output(
            ["colima", "ssh", "--", "df", "-Pk", "/var/lib/containerd"], text=True
        )
        available_kib = int(output.splitlines()[-1].split()[3])
        if available_kib < 4 * 1024 * 1024:
            raise RuntimeError(
                f"Docker VM has {available_kib / 1024:.0f} MiB free; allow at least 4 GiB "
                "for smoke images and data. Free Docker space, then rerun. "
                "No images or data were removed."
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=int, choices=list(TASK_FILES), default=1)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="run installed checks that need no database or network",
    )
    parser.add_argument(
        "--keep-services", action="store_true", help="leave started smoke services up"
    )
    args = parser.parse_args()
    env = dict(os.environ)
    env.setdefault("UV_CACHE_DIR", str(ROOT / ".cache" / "uv"))
    if args.offline:
        if not (ROOT / ".venv").is_dir():
            parser.error("Run uv sync --locked first to prepare the pinned environment.")
        env["UV_OFFLINE"] = "1"
        run(["uv", "run", "--locked", "--no-sync", "ruff", "check", "."], env)
        run(["uv", "run", "--locked", "--no-sync", "mypy", "src/domain_kg"], env)
        run(
            [
                "uv",
                "run",
                "--locked",
                "--no-sync",
                "pytest",
                *TASK_FILES[args.task],
                "-m",
                "not storage",
                "-q",
            ],
            env,
        )
        print("Offline checks passed. Real-store integration gates remain separate.")
        return 0

    check_docker_space()
    run([sys.executable, "scripts/dev_env.py"], env)
    run(["uv", "sync", "--locked"], env)
    previous = set(
        subprocess.check_output(
            ["docker", "compose", "ps", "--services", "--status", "running"],
            cwd=ROOT,
            text=True,
        ).split()
    )
    started = [name for name in SERVICES if name not in previous]
    print("Smoke limit: 2.25 GiB RAM / 2.5 CPU cores, tiny synthetic data, no local models.")
    try:
        run(
            [
                "docker",
                "compose",
                "up",
                "-d",
                "--wait",
                "--wait-timeout",
                "120",
                *SERVICES,
            ],
            env,
        )
        run(["uv", "run", "--locked", "--no-sync", "pytest", *TASK_FILES[args.task], "-q"], env)
    finally:
        if started and not args.keep_services:
            run(["docker", "compose", "stop", *started], env)
    print(f"Task {args.task} smoke passed against real local stores.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Smoke blocked: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
