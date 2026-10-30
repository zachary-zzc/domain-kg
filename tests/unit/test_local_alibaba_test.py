import subprocess

import pytest

from scripts import local_alibaba_test as runner


def test_local_default_ignores_ambient_live_flag_and_preserves_existing_services(monkeypatch):
    monkeypatch.setenv("DOMAIN_KG_ALIBABA_LIVE", "1")
    monkeypatch.setattr(runner, "check_docker_space", lambda: None)
    monkeypatch.setattr(runner, "check_memory_budget", lambda **kwargs: None)
    monkeypatch.setattr(runner.subprocess, "check_output", lambda *args, **kwargs: "mongo\n")
    commands = []

    def run(arguments, env, *, timeout):
        commands.append(arguments)
        assert env["DOMAIN_KG_ALIBABA_LIVE"] == "0"
        if "pytest" in arguments:
            raise subprocess.CalledProcessError(1, arguments)

    monkeypatch.setattr(runner, "run_owned", run)
    with pytest.raises(subprocess.CalledProcessError):
        runner.execute(live=False)
    assert commands[-1] == ["docker", "compose", "stop", "neo4j", "qdrant", "object-store"]


def test_live_requires_private_key_before_starting_any_service(monkeypatch, tmp_path):
    secret = tmp_path / "key"
    secret.write_text("fixture-key")
    secret.chmod(0o644)
    monkeypatch.setenv("DOMAIN_KG_ALIBABA_KEY_FILE", str(secret))

    def unexpected():
        pytest.fail("Docker must not be started before credential validation")

    monkeypatch.setattr(runner, "check_docker_space", unexpected)
    with pytest.raises(ValueError, match="owner-only"):
        runner.execute(live=True)
