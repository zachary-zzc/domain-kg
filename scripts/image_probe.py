"""Synthetic runtime checks streamed into a container, never shipped in its wheel."""

import asyncio
import base64
import hashlib
import importlib.resources
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

from domain_kg.ingestion.chunking import tokens
from domain_kg.ingestion.models import ParseOptions
from domain_kg.ingestion.parsers import Parser
from domain_kg.materials.models import BlobRef

FIXTURE_PAYLOADS = []


async def parse(data: bytes, media_type: str):
    blob = BlobRef(
        org_id=uuid4(),
        kg_id=uuid4(),
        material_id=uuid4(),
        version_id=uuid4(),
        object_id=uuid4(),
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        media_type=media_type,
    )

    class SyntheticOriginal:
        async def open(self, reference):
            assert reference == blob
            yield data

    return await Parser(SyntheticOriginal()).parse(blob, ParseOptions(timeout_seconds=20))


def main():
    assert os.getuid() == os.getgid() == 10001
    assert not os.access("/app", os.W_OK)
    assert not Path("/tests").exists() and not Path("/app/.env").exists()
    resources = importlib.resources.files("domain_kg.console")
    assert resources.joinpath("templates/operations.html").is_file()
    assert resources.joinpath("static/graph.js").is_file()
    connect = socket.create_connection

    def offline(*args, **kwargs):
        raise AssertionError("tokenization attempted a runtime network request")

    socket.create_connection = offline
    try:
        assert tokens("Offline bilingual evidence 中文") > 0
    finally:
        socket.create_connection = connect
    plain = asyncio.run(parse("Local evidence 中文".encode(), "text/plain"))
    assert plain.blocks[0].text == "Local evidence 中文"
    pdf = asyncio.run(parse(base64.b64decode(sys.argv[1], validate=True), "application/pdf"))
    assert [block.text for block in pdf.blocks] == [
        "First page evidence.",
        "Second page evidence.",
    ]
    assert [block.location.page for block in pdf.blocks] == [1, 2]
    assert "docling-parse" in pdf.parser_version
    scan = asyncio.run(parse(base64.b64decode(sys.argv[3], validate=True), "application/pdf"))
    scan_text = "".join(block.text for block in scan.blocks)
    assert "Scanned evidence" in scan_text and "扫描证据" in "".join(scan_text.split())
    assert scan.warnings == ("OCR_APPLIED",)
    assert all(
        block.location.page == 1 and block.location.text_origin == "ocr" for block in scan.blocks
    )
    office_formats = []
    assert FIXTURE_PAYLOADS
    for name, mime, encoded in FIXTURE_PAYLOADS:
        print("Checking synthetic format: " + name, file=sys.stderr, flush=True)
        document = asyncio.run(parse(base64.b64decode(encoded, validate=True), mime))
        assert document.blocks, name
        assert any("evidence" in b.text.lower() or "Alpha" in b.text for b in document.blocks), name
        if mime.startswith("image/") or name.startswith("slides."):
            assert document.images, name
        office_formats.append(name)
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as directory:
        private = Path(directory) / "private-canary"
        private.write_text("unreadable private probe")
        parser_root = Path(directory) / "parser"
        parser_root.mkdir()
        code = """import socket, subprocess, sys
from pathlib import Path
from domain_kg.ingestion.native_sandbox import confine
confine(Path(sys.argv[1]))
try:
    Path(sys.argv[2]).read_text()
except PermissionError:
    pass
else:
    raise AssertionError('private read escaped parser isolation')
try:
    socket.socket()
except PermissionError:
    pass
else:
    raise AssertionError('native socket escaped parser isolation')
read = 'from pathlib import Path; import sys; Path(sys.argv[1]).read_text()'
child = subprocess.run([sys.executable, '-c', read, sys.argv[2]], capture_output=True)
assert child.returncode != 0
"""
        subprocess.run(
            [sys.executable, "-c", code, str(parser_root), str(private)],
            check=True,
            capture_output=True,
            timeout=20,
        )
    worker = subprocess.run(
        [sys.executable, "-m", "domain_kg.worker", "--help"],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert "--operations-only" in worker.stdout and "--maintenance-only" in worker.stdout
    server = subprocess.Popen(
        json.loads(sys.argv[2]),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                with opener.open("http://127.0.0.1:8000/healthz", timeout=1) as result:
                    assert json.load(result) == {"status": "ok"}
                break
            except urllib.error.URLError:
                if server.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("image API failed its local liveness check") from None
                time.sleep(0.1)
        try:
            opener.open("http://127.0.0.1:8000/mcp", timeout=2)
        except urllib.error.HTTPError as error:
            assert error.code == 401
            assert "Bearer" in error.headers["WWW-Authenticate"]
        else:
            raise AssertionError("image allowed an unauthenticated MCP request")
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
    print(
        json.dumps(
            {
                "status": "passed",
                "uid": os.getuid(),
                "offline_tokenizer": True,
                "text_parser": True,
                "pdf_pages": len(pdf.blocks),
                "offline_bilingual_pdf_ocr": True,
                "office_and_image_formats": office_formats,
                "native_decoder_isolation": True,
                "worker_cli": True,
                "api_liveness": True,
                "unauthenticated_mcp": 401,
            }
        )
    )


if __name__ == "__main__":
    main()
