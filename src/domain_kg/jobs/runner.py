"""Single-job execution with heartbeats, exact checkpoint reuse and durable failures."""

import asyncio

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.ingestion.pipeline import Pipeline
from domain_kg.jobs.models import LeasedJob
from domain_kg.jobs.queue import JobQueue


class JobRunner:
    def __init__(self, queue: JobQueue, pipeline: Pipeline) -> None:
        self.queue, self.pipeline = queue, pipeline

    async def _steps(self, job: LeasedJob) -> None:
        if not self.pipeline.steps:
            self.pipeline.require_configured()
        for step in self.pipeline.steps:
            await self.queue.authorize(job)
            if await self.queue.load_checkpoint(job, step.stage, step.input_hash) is not None:
                continue
            async with asyncio.timeout(step.timeout_seconds):
                checkpoint = await step.execute(job)
            if checkpoint.stage != step.stage or checkpoint.input_hash != step.input_hash:
                raise DomainError(
                    ErrorCode.EVIDENCE_VALIDATION_FAILED,
                    "Pipeline output does not match its configured step.",
                )
            await self.queue.authorize(job)
            await self.queue.checkpoint(job, checkpoint)

    async def _heartbeat(self, job: LeasedJob) -> None:
        while True:
            await asyncio.sleep(min(10, self.queue.lease_seconds / 3))
            await self.queue.heartbeat(job)
            await self.queue.authorize(job)

    async def run_job(self, job: LeasedJob) -> None:
        work = asyncio.create_task(self._steps(job))
        heartbeat = asyncio.create_task(self._heartbeat(job))
        try:
            done, _ = await asyncio.wait((work, heartbeat), return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                await heartbeat  # Lost lease/revocation cancels any ongoing step.
            await work
            await self.queue.finish(job)
        except asyncio.CancelledError:
            # Worker/request death leaves the durable command recoverable after
            # lease expiration. Only explicit authorized cancel changes its state.
            raise
        except Exception as exc:
            failure = (
                exc
                if isinstance(exc, DomainError)
                else DomainError(
                    ErrorCode.BACKEND_UNAVAILABLE, "The worker step did not finish.", retryable=True
                )
            )
            try:
                await self.queue.fail(
                    job, failure, repair=failure.code == ErrorCode.REVISION_NOT_READY
                )
            except DomainError as stale:
                if stale.code != ErrorCode.VERSION_CONFLICT:
                    raise
        finally:
            work.cancel()
            heartbeat.cancel()
            await asyncio.gather(work, heartbeat, return_exceptions=True)
