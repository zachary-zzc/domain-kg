"""Legacy Office normalization inside the offline parser sandbox/process group."""

import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.ingestion.models import ParseOptions


def office_executable() -> Path | None:
    candidates = [
        Path("/usr/lib/libreoffice/program/soffice.bin"),
        Path("/usr/bin/libreoffice"),
        Path("/usr/bin/soffice"),
        Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"),
    ]
    installed = shutil.which("soffice")
    if installed:
        path = Path(installed).resolve()
        bundled = (
            path.parents[2]
            / "native/libreoffice-headless/libreoffice/LibreOfficeDev.app/Contents/MacOS/soffice"
        )
        candidates.extend([bundled, path])
    return next((path.resolve() for path in candidates if path.is_file()), None)


def normalize(data: bytes, source_ext: str, target_ext: str, options: ParseOptions) -> bytes:
    configured = os.environ.get("DOMAIN_KG_OFFICE_EXECUTABLE")
    executable = Path(configured) if configured else office_executable()
    if executable is None or not executable.is_file():
        raise DomainError(
            ErrorCode.PROVIDER_UNAVAILABLE, "Office conversion requires local LibreOffice."
        )
    with TemporaryDirectory(prefix="office-", dir=os.environ.get("TMPDIR")) as temporary:
        directory = Path(temporary)
        source = directory / ("source." + source_ext)
        source.write_bytes(data)
        profile = directory / "profile"
        profile.mkdir()
        (profile / "user").mkdir()
        (profile / "user/registrymodifications.xcu").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
            '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
            '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>'
            "</item></oor:items>"
        )
        try:
            # Exit 81 requests the same bounded first-profile restart as the
            # official launcher. No persistent process or shared profile is used.
            for _ in range(2):
                result = subprocess.run(
                    [
                        str(executable),
                        "-env:UserInstallation=" + profile.as_uri(),
                        "-env:OSL_SOCKET_PATH=" + str(directory.parent),
                        "--headless",
                        "--nologo",
                        "--nodefault",
                        "--norestore",
                        "--convert-to",
                        target_ext,
                        "--outdir",
                        str(directory),
                        str(source),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=min(options.timeout_seconds, 60),
                    check=False,
                    cwd=directory,
                    env=os.environ
                    | {"OSL_SOCKET_PATH": str(directory.parent), "SAL_USE_VCLPLUGIN": "svp"},
                )
                if result.returncode != 81:
                    break
        except subprocess.TimeoutExpired as exc:
            raise DomainError(
                ErrorCode.QUOTA_EXCEEDED, "Office conversion runtime limit exceeded."
            ) from exc
        except OSError as exc:
            raise DomainError(
                ErrorCode.PROVIDER_UNAVAILABLE, "Local Office conversion failed."
            ) from exc
        output = directory / ("source." + target_ext)
        if result.returncode != 0 or not output.is_file():
            raise DomainError(
                ErrorCode.UNSUPPORTED_FORMAT,
                "Office document could not be converted; check encryption or damage.",
            )
        if output.stat().st_size > options.max_expanded_bytes:
            raise DomainError(
                ErrorCode.QUOTA_EXCEEDED, "Converted Office document exceeds the parser limit."
            )
        return output.read_bytes()
