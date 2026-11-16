"""A sandboxed parser child uses the same committed code as its parent."""

import asyncio
import hashlib
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from test_preview_snapshot import committed_fixture, loader

from domain_kg.errors import DomainError
from domain_kg.ingestion.models import ParseOptions
from domain_kg.materials.models import BlobRef


async def test_native_parser_reads_its_own_snapshot_without_ambient_pythonpath(
    tmp_path, monkeypatch
):
    import domain_kg.ingestion.parsers as parser_module

    repository = Path(__file__).parents[2]
    # Freeze the code under test as one coherent committed fixture. Freezing
    # the user's HEAD mixed an older worker/schema with current ParseOptions.
    source = await asyncio.to_thread(committed_fixture, tmp_path)
    await asyncio.to_thread(
        shutil.copytree,
        repository / "src",
        source / "src",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    await asyncio.to_thread(
        subprocess.run, ["git", "add", "src"], cwd=source, check=True, capture_output=True
    )
    await asyncio.to_thread(
        subprocess.run,
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
            "current parser fixture",
        ],
        cwd=source,
        check=True,
        capture_output=True,
    )
    snapshot = tmp_path / "snapshot"
    loader().freeze(source, snapshot)
    monkeypatch.setattr(
        parser_module, "__file__", str(snapshot / "src/domain_kg/ingestion/parsers.py")
    )
    monkeypatch.setenv("PYTHONPATH", "/untrusted/ambient-code")
    data = b"Tiny frozen parser."
    blob = BlobRef(
        org_id=uuid4(),
        kg_id=uuid4(),
        material_id=uuid4(),
        version_id=uuid4(),
        object_id=uuid4(),
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        media_type="text/plain",
    )

    class BytesOnly:
        async def open(self, reference):
            assert reference == blob
            yield data

    worker = snapshot / "src/domain_kg/ingestion/parser_worker.py"
    original = worker.read_text()
    marker = 'print(json.dumps({"ok": True}))'
    assert marker in original
    worker.write_text(
        original.replace(
            marker, 'raise DomainError(ErrorCode.UNSUPPORTED_FORMAT, "Frozen parser marker")'
        )
    )
    with pytest.raises(DomainError, match="Frozen parser marker"):
        await parser_module.Parser(BytesOnly()).parse(blob, ParseOptions(timeout_seconds=10))
    worker.write_text(original)
    document = await parser_module.Parser(BytesOnly()).parse(blob, ParseOptions(timeout_seconds=10))
    assert document.blob == blob and document.blocks[0].text == data.decode()
