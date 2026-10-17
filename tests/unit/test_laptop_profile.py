"""The laptop profile cannot silently enlarge memory or enable costly model tests."""

import json
import os
import sys

import pytest
from test_smoke_budget import load_runner


def test_default_laptop_profile_is_bounded_and_has_no_automatic_model_calls():
    profile = load_runner().load_laptop_profile()
    assert profile["docker"]["memory_cap_bytes"] == 1_500_000_000
    assert sum(profile["docker"]["service_memory_bytes"].values()) == 1_476_395_008
    assert profile["tests"]["provider_mode"] == "synthetic"
    assert profile["tests"]["preview_restore_demo"] is False
    assert profile["native"]["autostart"] is False
    assert profile["native"]["max_concurrent_models"] == 1
    assert profile["native"]["embedding_tiny_requests"] == 3
    assert profile["native"]["chat_tiny_max_output_tokens"] == 128


@pytest.mark.parametrize(
    ("area", "field", "value"),
    [
        ("docker", "memory_cap_bytes", 1_500_000_001),
        ("docker", "memory_cap_bytes", True),
        ("native", "rss_cap_per_process_bytes", 0),
        ("native", "max_concurrent_models", 2),
        ("native", "threads", 8),
        ("native", "embedding_tiny_requests", 100),
        ("native", "autostart", True),
        ("tests", "preview_restore_demo", 1),
        ("tests", "preview_restore_demo", "false"),
    ],
)
def test_unsafe_laptop_profile_is_rejected_before_startup(tmp_path, area, field, value):
    runner = load_runner()
    profile = runner.load_laptop_profile()
    profile[area][field] = value
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile))
    with pytest.raises(RuntimeError):
        runner.load_laptop_profile(path)


def test_smoke_timeout_stops_the_owned_child_process(tmp_path):
    marker = tmp_path / "child-stopped"
    child = (
        "import signal,time,sys; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, lambda *_: "
        f"(Path({str(marker)!r}).write_text('stopped'), sys.exit(0))); "
        "time.sleep(60)"
    )
    parent = (
        "import subprocess,time,sys; "
        "subprocess.Popen([sys.executable,'-c',sys.argv[1]]); time.sleep(60)"
    )
    with pytest.raises(RuntimeError, match="deadline"):
        load_runner().run(
            [sys.executable, "-c", parent, child], dict(os.environ), timeout_seconds=1
        )
    assert marker.read_text() == "stopped"
