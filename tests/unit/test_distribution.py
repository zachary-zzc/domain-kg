"""Distribution inspection rejects developer state and private paths without extraction."""

import io
import runpy
import tarfile
import zipfile
from pathlib import Path

import pytest

CHECKER = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "scripts/check_distribution.py")
)["check"]


def archives(folder, extra=None):
    with zipfile.ZipFile(folder / "application.whl", "w") as wheel:
        for name in (
            "domain_kg/app.py",
            "domain_kg/console/templates/operations.html",
            "domain_kg/console/static/app.css",
        ):
            wheel.writestr(name, b"synthetic")
    with tarfile.open(folder / "application.tar.gz", "w:gz") as source:
        for name in (
            "application/src/domain_kg/app.py",
            "application/pyproject.toml",
            *(extra or ()),
        ):
            body = b"synthetic"
            info = tarfile.TarInfo(name)
            info.size = len(body)
            source.addfile(info, io.BytesIO(body))


def test_checked_distribution_includes_application_and_console(tmp_path):
    archives(tmp_path)
    CHECKER(tmp_path)


@pytest.mark.parametrize(
    "path",
    [
        "application/.superpowers/debug.log",
        "application/.env",
        "application/src/../.env",
        "application/secrets/private.json",
    ],
)
def test_private_development_paths_never_enter_a_distribution(tmp_path, path):
    archives(tmp_path, (path,))
    with pytest.raises(ValueError):
        CHECKER(tmp_path)
