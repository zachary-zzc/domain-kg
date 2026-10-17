"""A live preview imports a frozen committed tree without copying local secrets."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest


def loader():
    path = Path(__file__).resolve().parents[2] / "scripts/preview.py"
    spec = importlib.util.spec_from_file_location("domain_kg_preview", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def committed_fixture(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    for name in ("src", "tests", "config"):
        (root / name).mkdir()
    (root / "src/value.py").write_text("VALUE = 'committed'\n")
    (root / "tests/test_value.py").write_text("# synthetic fixture\n")
    (root / "config/laptop-smoke.json").write_text("{}")
    (root / ".env").write_text("FIXTURE_ONLY=not-a-real-secret\n")
    for arguments in (
        ("init", "-b", "codex/preview-fixture"),
        ("add", "."),
        (
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            "seed",
        ),
    ):
        subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True)
    return root


def test_snapshot_uses_commit_and_survives_working_source_edits(tmp_path):
    module = loader()
    root = committed_fixture(tmp_path)
    (root / "src/value.py").write_text("VALUE = 'working edit'\n")
    snapshot = tmp_path / "snapshot"
    revision = module.freeze(root, snapshot)
    assert len(revision) in (40, 64)
    assert (snapshot / "src/value.py").read_text() == "VALUE = 'committed'\n"
    (root / "src/value.py").write_text("VALUE = 'later edit'\n")
    assert "committed" in (snapshot / "src/value.py").read_text()
    assert not (snapshot / ".env").exists() and not (snapshot / ".git").exists()


def test_snapshot_refuses_links_before_extracting_anything(tmp_path):
    module = loader()
    root = committed_fixture(tmp_path)
    (root / "src/link").symlink_to(root / ".env")
    subprocess.run(["git", "add", "src/link"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            "link fixture",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    snapshot = tmp_path / "snapshot"
    with pytest.raises(RuntimeError):
        module.freeze(root, snapshot)
    assert not snapshot.exists()


async def test_snapshot_budget_refuses_oversize_before_extracting(tmp_path, monkeypatch):
    module = loader()
    root = committed_fixture(tmp_path)
    monkeypatch.setattr(module, "MAX_BYTES", 1024)
    snapshot = tmp_path / "snapshot"
    with pytest.raises(RuntimeError, match="budget"):
        module.freeze(root, snapshot)
    assert not snapshot.exists()


def test_preview_deadline_stops_only_its_owned_child(tmp_path):
    marker = tmp_path / "stopped"
    code = (
        "import signal,sys,time; from pathlib import Path; "
        "signal.signal(signal.SIGINT, lambda *_: "
        f"(Path({str(marker)!r}).write_text('stopped'), sys.exit(0))); time.sleep(60)"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        loader().run_owned([sys.executable, "-c", code], dict(os.environ), timeout=1)
    assert marker.read_text() == "stopped"
