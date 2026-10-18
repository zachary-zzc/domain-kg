"""Check an already built local image with no external network or private mounts."""

import argparse
import base64
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check(image: str) -> dict:
    if not image or image.startswith("-"):
        raise ValueError("an explicit local image name is required")
    metadata = json.loads(subprocess.check_output(["docker", "image", "inspect", image]))[0]
    if metadata["Config"]["User"] != "10001:10001":
        raise ValueError("application image must run as the dedicated non-root user")
    fixture = base64.b64encode((ROOT / "tests/fixtures/materials/native.pdf").read_bytes()).decode()
    probe = (ROOT / "scripts/image_probe.py").read_bytes()
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--pull",
            "never",
            "--interactive",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "64",
            "--memory",
            "512m",
            "--memory-swap",
            "512m",
            "--cpus",
            "1",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=128m",
            "--entrypoint",
            "python",
            image,
            "-",
            fixture,
            json.dumps(metadata["Config"]["Cmd"]),
        ],
        input=probe,
        capture_output=True,
        timeout=120,
        check=True,
    )
    report = json.loads(result.stdout.splitlines()[-1])
    return {"image_id": metadata["Id"], "architecture": metadata["Architecture"], **report}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = check(args.image)
    except subprocess.CalledProcessError as error:
        # The probe has only bundled synthetic inputs; preserve its actionable failure.
        if error.stderr:
            print(error.stderr.decode(), end="")
        raise SystemExit("Local image runtime verification failed.") from None
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(text)
    print(text, end="")
