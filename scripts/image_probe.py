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
                "worker_cli": True,
                "api_liveness": True,
                "unauthenticated_mcp": 401,
            }
        )
    )


if __name__ == "__main__":
    main()
