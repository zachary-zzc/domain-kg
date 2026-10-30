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
    scan = base64.b64encode(
        (ROOT / "tests/fixtures/materials/scanned-bilingual.pdf").read_bytes()
    ).decode()
    from domain_kg.ingestion.file_types import DOCX, ODP, ODS, ODT, PPTX, XLSX

    cases = [
        (
            name,
            mime,
            base64.b64encode((ROOT / "tests/fixtures/materials" / name).read_bytes()).decode(),
        )
        for name, mime in [
            ("headings-table.docx", DOCX),
            ("headings-table.doc", "application/msword"),
            ("headings-table.rtf", "application/rtf"),
            ("headings-table.odt", ODT),
            ("workbook.xlsx", XLSX),
            ("workbook.xls", "application/vnd.ms-excel"),
            ("workbook.ods", ODS),
            ("slides.pptx", PPTX),
            ("slides.ppt", "application/vnd.ms-powerpoint"),
            ("slides.odp", ODP),
            ("scanned-bilingual.png", "image/png"),
        ]
    ]
    template = (ROOT / "scripts/image_probe.py").read_text()
    if template.count("FIXTURE_PAYLOADS = []") != 1:
        raise ValueError("image probe fixture placeholder differs")
    probe = template.replace("FIXTURE_PAYLOADS = []", "FIXTURE_PAYLOADS = " + repr(cases)).encode()
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
            "/run/domain-kg:rw,noexec,nosuid,size=128m,uid=10001,gid=10001,mode=0700",
            "--entrypoint",
            "python",
            image,
            "-",
            fixture,
            json.dumps(metadata["Config"]["Cmd"]),
            scan,
        ],
        input=probe,
        capture_output=True,
        timeout=240,
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
