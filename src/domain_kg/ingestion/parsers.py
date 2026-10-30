"""Verified originals through one offline, resource-bounded native parser process."""

import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.ingestion.models import ParsedDocument, ParseOptions
from domain_kg.materials.models import BlobRef
from domain_kg.storage.blobs import BlobStore

MAX_RSS_BYTES = 384 * 1024 * 1024


class Parser:
    def __init__(self, blobs: BlobStore) -> None:
        self.blobs = blobs
        self._limit = asyncio.Semaphore(1)

    @staticmethod
    def _command(directory: Path, arguments: list[str]) -> list[str]:
        if sys.platform != "darwin":
            return arguments
        # Read package/runtime code, the system libraries and this temporary
        # original. The child cannot read the user's home or write elsewhere.
        directory = directory.resolve()
        allowed = [
            str(Path(sys.prefix).resolve()),
            str(Path(sys.base_prefix).resolve()),
            str(Path(__file__).resolve().parents[2]),
        ]
        from domain_kg.ingestion.conversion import office_executable

        office = office_executable()
        if office is not None:
            allowed.append(
                str(office.parents[2] if office.parent.name == "MacOS" else office.parent)
            )
        profile = "(version 1)(allow default)(deny network*)(deny file-write*)"
        profile += f"(allow file-write* (subpath {json.dumps(str(directory))}))"
        profile += '(allow file-write* (literal "/dev/null"))'
        profile += f"(deny file-read* (subpath {json.dumps(str(Path.home()))}))"
        profile += (
            "(allow file-read* "
            + " ".join(f"(subpath {json.dumps(path)})" for path in allowed)
            + ")"
        )
        if office is not None:
            # UNO resolves its installation through ancestor directory metadata.
            # This grants no private directory listing or file content access.
            profile += (
                "(allow file-read-metadata "
                + " ".join(f"(literal {json.dumps(str(path))})" for path in office.parents)
                + ")"
            )
        return ["/usr/bin/sandbox-exec", "-p", profile, *arguments]

    @staticmethod
    async def _rss(process: asyncio.subprocess.Process) -> int:
        def linux_group_rss() -> int:
            total = 0
            for entry in Path("/proc").iterdir():
                if not entry.name.isdecimal():
                    continue
                try:
                    # The command name may contain spaces or parentheses.
                    fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                    if int(fields[2]) == process.pid:
                        total += int(fields[21]) * os.sysconf("SC_PAGE_SIZE")
                except (FileNotFoundError, ProcessLookupError, PermissionError):
                    continue  # Processes may exit while the group is sampled.
            return total

        if sys.platform != "darwin":
            return await asyncio.to_thread(linux_group_rss)
        check = await asyncio.create_subprocess_exec(
            "/bin/ps",
            "-o",
            "rss=",
            "-g",
            str(process.pid),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        output, _ = await check.communicate()
        if check.returncode != 0 and process.returncode is None:
            raise DomainError(
                ErrorCode.BACKEND_UNAVAILABLE, "Parser memory monitoring is unavailable."
            )
        try:
            return sum(int(line) for line in output.splitlines()) * 1024
        except ValueError as exc:
            raise DomainError(
                ErrorCode.BACKEND_UNAVAILABLE, "Parser memory monitoring is unavailable."
            ) from exc

    async def parse(self, blob: BlobRef, options: ParseOptions) -> ParsedDocument:
        blob, options = BlobRef.model_validate(blob), ParseOptions.model_validate(options)
        async with self._limit:
            with TemporaryDirectory(prefix="kg-parser-") as temporary:
                directory = await asyncio.to_thread(Path(temporary).resolve)
                original, output = directory / "original", directory / "parsed.json"
                with original.open("wb") as stream:
                    async for part in self.blobs.open(blob):
                        await asyncio.to_thread(stream.write, part)
                arguments = [
                    sys.executable,
                    "-m",
                    "domain_kg.ingestion.parser_worker",
                    str(original),
                    str(output),
                    blob.model_dump_json(),
                    options.model_dump_json(),
                ]
                code_root = (await asyncio.to_thread(Path(__file__).resolve)).parents[2]
                environment = {
                    "PATH": os.defpath,
                    "LC_ALL": "C",
                    "PYTHONUTF8": "1",
                    # Bind the child to this server's trusted code, including
                    # frozen previews; ambient editable installs are not authority.
                    "PYTHONPATH": str(code_root),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONHASHSEED": "0",
                    "TMPDIR": str(directory),
                    "XDG_CACHE_HOME": str(directory / "cache"),
                    "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1",
                    "OMP_NUM_THREADS": "1",
                    "OMP_THREAD_LIMIT": "1",
                    "OPENBLAS_NUM_THREADS": "1",
                    "MKL_NUM_THREADS": "1",
                    "HOME": str(directory),
                }
                from domain_kg.ingestion.conversion import office_executable

                office = office_executable()
                if office is not None:
                    environment["DOMAIN_KG_OFFICE_EXECUTABLE"] = str(office)
                process = await asyncio.create_subprocess_exec(
                    *self._command(directory, arguments),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=environment,
                    cwd=directory,
                    start_new_session=True,
                )
                stdout = process.stdout
                assert stdout is not None

                async def response() -> bytes:
                    content = bytearray()
                    while part := await stdout.read(1024):
                        content.extend(part)
                        if len(content) > 8192:
                            raise DomainError(
                                ErrorCode.UNSUPPORTED_FORMAT, "Invalid parser response."
                            )
                    return bytes(content)

                read = asyncio.create_task(response())
                waiter = asyncio.create_task(process.wait())
                try:
                    async with asyncio.timeout(options.timeout_seconds):
                        while not waiter.done():
                            if read.done():
                                read.result()
                            if await self._rss(process) > MAX_RSS_BYTES:
                                raise DomainError(
                                    ErrorCode.QUOTA_EXCEEDED, "Parser memory limit exceeded."
                                )
                            await asyncio.sleep(0.2)
                        status: dict[str, Any] = json.loads(await read)
                    if process.returncode != 0 or not status.get("ok"):
                        raise DomainError(
                            ErrorCode(status.get("code", "UNSUPPORTED_FORMAT")),
                            status.get("message", "The parser could not process this original."),
                        )
                    if not output.exists() or output.stat().st_size > 100 * 1024 * 1024:
                        raise DomainError(
                            ErrorCode.QUOTA_EXCEEDED, "Parsed output exceeds the resource limit."
                        )
                    parsed = ParsedDocument.model_validate_json(
                        await asyncio.to_thread(output.read_bytes)
                    )
                    if parsed.blob != blob:
                        raise DomainError(
                            ErrorCode.EVIDENCE_VALIDATION_FAILED, "Parsed source ownership changed."
                        )
                    return parsed
                except TimeoutError as exc:
                    raise DomainError(
                        ErrorCode.QUOTA_EXCEEDED, "Parser runtime limit exceeded."
                    ) from exc
                except (ValueError, OSError) as exc:
                    raise DomainError(
                        ErrorCode.UNSUPPORTED_FORMAT,
                        "The native parser did not return a valid document.",
                    ) from exc
                finally:
                    # A failed/killed parser can leave an OCR child behind.
                    # Always clear its group, even when the parent already exited.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await process.wait()
                    read.cancel()
                    waiter.cancel()
                    await asyncio.gather(read, waiter, return_exceptions=True)
