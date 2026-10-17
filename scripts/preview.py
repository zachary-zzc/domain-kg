"""Serve a synthetic preview from an immutable committed source snapshot."""

import argparse
import importlib.util
import os
import re
import signal
import subprocess
import sys
import tarfile
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory, TemporaryFile

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ROOTS = {
    "src",
    "tests",
    "scripts",
    "config",
    "pyproject.toml",
    "uv.lock",
    "requirements.lock",
}
MAX_BYTES = 16 * 1024 * 1024
CHILD = """
import sys
from pathlib import Path
import domain_kg
root = Path(sys.argv[1]).resolve() / 'src'
if any(not Path(value).resolve().is_relative_to(root) for value in domain_kg.__path__):
    raise RuntimeError('Preview package imports must stay inside its source snapshot.')
import importlib.util
application = importlib.util.find_spec('domain_kg.app')
if application is None or not Path(application.origin).resolve().is_relative_to(root):
    raise RuntimeError('Preview application imports must stay inside its source snapshot.')
import pytest
raise SystemExit(pytest.main(sys.argv[2:]))
"""


def freeze(repository: Path, output: Path) -> str:
    revision = subprocess.check_output(
        ["git", "rev-parse", "--verify", "HEAD"], cwd=repository, text=True
    ).strip()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision):
        raise RuntimeError("Preview requires a committed source revision.")
    entries = subprocess.check_output(
        ["git", "ls-tree", "--name-only", revision], cwd=repository, text=True
    ).splitlines()
    selected = sorted(PUBLIC_ROOTS.intersection(entries))
    if not {"src", "tests", "config"}.issubset(selected):
        raise RuntimeError("Preview source roots are missing.")
    with TemporaryFile() as archive:
        process = subprocess.Popen(
            ["git", "archive", "--format=tar", revision, "--", *selected],
            cwd=repository,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert process.stdout is not None
        try:
            size = 0
            while block := process.stdout.read(65536):
                size += len(block)
                if size > MAX_BYTES:
                    raise RuntimeError("Preview source exceeds its snapshot budget.")
                archive.write(block)
            if process.wait(timeout=10):
                raise RuntimeError("Committed preview source could not be archived.")
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        archive.seek(0)
        with tarfile.open(fileobj=archive) as bundle:
            members = []
            total_size = 0
            for member in bundle:
                members.append(member)
                total_size += member.size
                if len(members) > 5000 or total_size > MAX_BYTES:
                    raise RuntimeError("Preview source exceeds its snapshot budget.")
                path = PurePosixPath(member.name)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or path.parts[0] not in PUBLIC_ROOTS
                    or not (member.isfile() or member.isdir())
                    or any(
                        part in (".git", ".env") or part.startswith(".env.") or ".local." in part
                        for part in path.parts
                    )
                ):
                    raise RuntimeError("Preview snapshot contains an unsupported source entry.")
            output.mkdir(parents=True, exist_ok=False)
            bundle.extractall(output, members=members, filter="data")
    return revision


def run_owned(arguments: list[str], env: dict[str, str], *, timeout: int) -> None:
    process = subprocess.Popen(arguments, cwd=ROOT, env=env, start_new_session=True)
    try:
        result = process.wait(timeout=timeout)
    except BaseException:
        for stop, grace in ((signal.SIGINT, 10), (signal.SIGTERM, 5), (signal.SIGKILL, 5)):
            if process.poll() is not None:
                break
            try:
                os.killpg(process.pid, stop)
                process.wait(timeout=grace)
            except ProcessLookupError:
                break
            except subprocess.TimeoutExpired:
                continue
        raise
    if result:
        raise subprocess.CalledProcessError(result, arguments)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    # Reuse the canonical budget validator without importing the application.
    spec = importlib.util.spec_from_file_location("preview_budgets", ROOT / "scripts/smoke.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("The laptop budget validator is unavailable.")
    budgets = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(budgets)
    with TemporaryDirectory(prefix="domain-kg-preview-") as temporary:
        snapshot = Path(temporary) / "source"
        revision = freeze(ROOT, snapshot)
        profile = budgets.load_laptop_profile(snapshot / "config/laptop-smoke.json")
        budgets.check_memory_budget(running=True, profile=profile)
        print(f"Frozen preview revision: {revision}", flush=True)
        environment = dict(os.environ) | {
            "DOMAIN_KG_BROWSER_PREVIEW": "1",
            "DOMAIN_KG_PREVIEW_REVISION": revision,
            "PYTHONPATH": os.pathsep.join(
                (str(snapshot / "src"), str(snapshot / "tests/integration"))
            ),
        }

        def interrupted(*_: object) -> None:
            raise KeyboardInterrupt

        previous = signal.signal(signal.SIGTERM, interrupted)
        try:
            run_owned(
                [
                    sys.executable,
                    "-c",
                    CHILD,
                    str(snapshot),
                    "--rootdir",
                    str(snapshot),
                    str(snapshot / "tests/evaluation/test_console_browser.py"),
                    "-q",
                    "-s",
                ],
                environment,
                timeout=profile["tests"]["task_timeout_seconds"],
            )
        finally:
            signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    main()
