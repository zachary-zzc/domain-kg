"""Owned native CLI lifecycle for explicit laptop runs; never an API tool."""

import asyncio
import os
from pathlib import Path
from typing import BinaryIO

from domain_kg.errors import DomainError, ErrorCode


async def rss_bytes(pid: int) -> int:
    process = await asyncio.create_subprocess_exec(
        "/bin/ps",
        "-o",
        "rss=",
        "-p",
        str(pid),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    async with asyncio.timeout(2):
        output, _ = await process.communicate()
    if process.returncode != 0 or not output.strip():
        raise RuntimeError("Native RSS measurement is unavailable.")
    return int(output.strip()) * 1024


class OwnedProcess:
    def __init__(
        self,
        command: list[str],
        log_path: Path,
        *,
        rss_cap_bytes: int,
        sample_seconds: float = 0.25,
    ) -> None:
        if rss_cap_bytes <= 0 or not 0.02 <= sample_seconds <= 1:
            raise ValueError("Invalid native watchdog budget.")
        self.command, self.log_path = command, log_path
        self.cap, self.interval = rss_cap_bytes, sample_seconds
        self.peak_rss_bytes = 0
        self.exceeded = False
        self.monitor_failed = False
        self.process: asyncio.subprocess.Process
        self._watcher: asyncio.Task[None] | None = None
        self._log: BinaryIO | None = None

    async def __aenter__(self) -> "OwnedProcess":
        self.log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._log = self.log_path.open("ab")
        os.chmod(self.log_path, 0o600)
        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=self._log,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        except BaseException:
            self._log.close()
            raise
        self._watcher = asyncio.create_task(self._watch())
        return self

    async def _watch(self) -> None:
        while self.process.returncode is None:
            try:
                used = await rss_bytes(self.process.pid)
                self.peak_rss_bytes = max(self.peak_rss_bytes, used)
                if used > self.cap:
                    self.exceeded = True
                    self.process.terminate()
                    return
            except (RuntimeError, ValueError, OSError, TimeoutError):
                if self.process.returncode is None:
                    self.monitor_failed = True
                    self.process.terminate()
                return
            await asyncio.sleep(self.interval)

    async def require_alive(self) -> None:
        if self.process.returncode is not None or self.exceeded or self.monitor_failed:
            raise DomainError(ErrorCode.PROVIDER_UNAVAILABLE, "Owned native model is unavailable.")

    async def __aexit__(self, *_: object) -> None:
        if self._watcher is not None:
            self._watcher.cancel()
            await asyncio.gather(self._watcher, return_exceptions=True)
        try:
            if self.process.returncode is None:
                self.process.terminate()
                try:
                    async with asyncio.timeout(10):
                        await self.process.wait()
                except TimeoutError:
                    self.process.kill()
                    await self.process.wait()
            else:
                await self.process.wait()
        finally:
            if self._log is not None:
                self._log.close()
