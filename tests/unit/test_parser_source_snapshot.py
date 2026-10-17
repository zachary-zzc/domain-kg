"""A sandboxed parser child uses the same committed code as its parent."""

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from test_preview_snapshot import loader

from domain_kg.errors import DomainError
from domain_kg.ingestion.models import ParseOptions
from domain_kg.materials.models import BlobRef


async def test_native_parser_reads_its_own_snapshot_without_ambient_pythonpath(
    tmp_path, monkeypatch
):
    import domain_kg.ingestion.parsers as parser_module

    repository = Path(__file__).parents[2]
    snapshot = tmp_path / "snapshot"
    loader().freeze(repository, snapshot)
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
