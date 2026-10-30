"""Offline raster OCR shared by image and PDF adapters; run inside parser isolation."""

import shutil
import subprocess
from tempfile import TemporaryFile

from domain_kg.errors import DomainError, ErrorCode

MAX_OCR_OUTPUT = 1024 * 1024


def recognize_raster(data: bytes, *, engine: str | None = None) -> str:
    engine = engine or shutil.which("tesseract", path="/usr/bin:/opt/homebrew/bin:/usr/local/bin")
    if engine is None:
        raise DomainError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "Local OCR requires Tesseract with English and simplified Chinese language data.",
        )
    with TemporaryFile() as output:
        try:
            process = subprocess.run(
                [engine, "stdin", "stdout", "-l", "eng+chi_sim", "--psm", "3"],
                input=data,
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise DomainError(
                ErrorCode.QUOTA_EXCEEDED, "Local OCR runtime limit exceeded."
            ) from exc
        except OSError as exc:
            raise DomainError(ErrorCode.PROVIDER_UNAVAILABLE, "Local OCR failed.") from exc
        if process.returncode != 0:
            raise DomainError(ErrorCode.PROVIDER_UNAVAILABLE, "Local OCR failed.")
        output.seek(0)
        text = output.read(MAX_OCR_OUTPUT + 1)
    if len(text) > MAX_OCR_OUTPUT:
        raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Local OCR output limit exceeded.")
    return text.decode("utf-8")
