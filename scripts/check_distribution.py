"""Verify source/wheel boundaries and required console assets without executing archives."""

import hashlib
import json
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

FORBIDDEN = {".env", ".git", ".superpowers", ".venv", "__pycache__", "secrets", "uploads"}
SOURCE_ROOTS = {
    ".gitignore",
    "src",
    "pyproject.toml",
    "README.md",
    "uv.lock",
    "requirements.lock",
    "stubs",
    "PKG-INFO",
}


def check(folder: Path) -> None:
    artifacts = sorted((*folder.glob("*.whl"), *folder.glob("*.tar.gz")))
    if len(artifacts) != 2:
        raise ValueError("expected one wheel and one source distribution")
    for artifact in artifacts:
        if artifact.stat().st_size > 64 * 1024**2:
            raise ValueError("distribution exceeds its inspection budget")
        if artifact.name.endswith(".whl"):
            with zipfile.ZipFile(artifact) as archive:
                names = archive.namelist()
                size = sum(item.file_size for item in archive.infolist())
            if not {
                "domain_kg/app.py",
                "domain_kg/console/templates/operations.html",
                "domain_kg/console/templates/identity_integrations.html",
                "domain_kg/console/templates/identity_connections.html",
                "domain_kg/identity/bridge/routes.py",
            }.issubset(names) or not any(
                name.startswith("domain_kg/console/static/") for name in names
            ):
                raise ValueError("wheel omits required application or console assets")
        else:
            with tarfile.open(artifact) as archive:
                members = []
                for member in archive:
                    members.append(member)
                    if len(members) > 2000 or member.size > 64 * 1024**2:
                        raise ValueError("distribution exceeds its inspection budget")
                if any(item.issym() or item.islnk() for item in members):
                    raise ValueError("source distribution contains links")
                names = [item.name for item in members]
                size = sum(item.size for item in members)
            for name in names:
                parts = PurePosixPath(name).parts
                if (
                    len(parts) > 1
                    and parts[1] not in SOURCE_ROOTS
                    and not parts[1].startswith("LICENSE")
                ):
                    raise ValueError("source distribution includes excluded paths")
        if len(names) > 2000 or size > 64 * 1024**2:
            raise ValueError("distribution exceeds its inspection budget")
        for name in names:
            path = PurePosixPath(name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or any(part in FORBIDDEN or part.startswith(".env.") for part in path.parts)
            ):
                raise ValueError("distribution includes excluded paths")
        checksum = hashlib.sha256()
        with artifact.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                checksum.update(chunk)
        print(
            json.dumps(
                {
                    "artifact": artifact.name,
                    "files": len(names),
                    "sha256": checksum.hexdigest(),
                }
            )
        )


if __name__ == "__main__":
    check(Path(sys.argv[1]))
