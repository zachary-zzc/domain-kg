"""Run the bounded local report without model calls or automatic service startup."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from eval.configuration import load
from scripts.smoke import check_memory_budget

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "eval/pilot.toml")
    parser.add_argument("--output", type=Path, default=ROOT / ".cache/evaluation")
    args = parser.parse_args()
    config_path = args.config.resolve()
    config, _, _ = load(config_path)
    check_memory_budget(running=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = output / "report.json"
    # A failed run cannot leave an earlier report looking freshly successful.
    report.unlink(missing_ok=True)
    (output / "report.pending.json").unlink(missing_ok=True)
    environment = dict(os.environ)
    environment["DOMAIN_KG_EVALUATION_CONFIG"] = str(config_path)
    environment["DOMAIN_KG_EVALUATION_REPORT"] = str(output / "report.pending.json")
    environment["PYTEST_ADDOPTS"] = ""
    log = output / "pytest.log"
    with log.open("wb") as stream:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/evaluation/test_release_gates.py",
                "-q",
                "--tb=short",
            ],
            cwd=ROOT,
            env=environment,
            stdout=stream,
            stderr=subprocess.STDOUT,
            timeout=config.timeout_seconds,
        )
    print(log.read_text()[-5000:])
    if result.returncode:
        return result.returncode
    pending = Path(environment["DOMAIN_KG_EVALUATION_REPORT"])
    content = json.loads(pending.read_bytes())
    if content["status"] != "passed" or content["scope"] != "local_synthetic_provider_correctness":
        raise ValueError("evaluation did not produce a verified local report")
    pending.replace(report)
    print(
        json.dumps(
            {"report": str(report), "held_out_summaries": content["held_out_summaries"]}, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
