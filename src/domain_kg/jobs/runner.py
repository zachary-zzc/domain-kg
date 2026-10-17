"""Single-job execution with heartbeats, exact checkpoint reuse and durable failures."""

import asyncio
from typing import Protocol

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.ingestion.pipeline import Pipeline
from domain_kg.jobs.models import LeasedJob
from domain_kg.jobs.queue import JobQueue


class OwnerOperationExecutor(Protocol):
    async def run(self, job: LeasedJob) -> None: ...


class JobRunner:
    def __init__(
        self,
        queue: JobQueue,
        pipeline: Pipeline,
        *,
        operations: OwnerOperationExecutor | None = None,
    ) -> None:
        self.queue, self.pipeline, self.operations = queue, pipeline, operations

    async def _steps(self, job: LeasedJob) -> None:
        if job.kind in (
            "backup",
            "export",
            "restore",
            "maintenance",
            "retention",
            "restore_cleanup",
            "generation",
        ):
            await self.queue.authorize(job)
            if self.operations is None:
                raise DomainError(
                    ErrorCode.REVISION_NOT_READY,
                    "The owner operation handler is not configured.",
                    retryable=True,
                )
            await self.operations.run(job)
            return
        if job.kind == "purge_material":
            from domain_kg.materials.purge import PurgeService

            await PurgeService(self.queue).run(job)
            return
        if not self.pipeline.steps:
            self.pipeline.require_configured()
        importing = job.kind == "import"
        if job.kind == "reprocess_material":
            await self.queue.authorize(job)
            raw = await self.queue._current(job)
            importing = (
                await self.queue.database.import_source_origins.find_one(
                    {
                        "org_id": str(job.org_id),
                        "kg_id": str(job.kg_id),
                        "material_id": raw["material_id"],
                        "version_id": str(job.target_id),
                    }
                )
                is not None
            )
        steps = (
            self.pipeline.import_steps
            if importing
            else self.pipeline.curation_steps
            if job.kind == "curate"
            else self.pipeline.restore_steps
            if job.kind == "restore_material"
            else self.pipeline.repair_steps
            if job.kind == "repair"
            else self.pipeline.steps
        )
        if not steps:
            raise DomainError(
                ErrorCode.PROVIDER_UNAVAILABLE, "This job pipeline is not configured."
            )
        for step in steps:
            await self.queue.authorize(job)
            input_hash = await step.hash_for(job)
            if await self.queue.load_checkpoint(job, step.stage, input_hash) is not None:
                continue
            async with asyncio.timeout(step.timeout_seconds):
                checkpoint = await step.execute(job)
            if checkpoint.stage != step.stage or checkpoint.input_hash != input_hash:
                raise DomainError(
                    ErrorCode.EVIDENCE_VALIDATION_FAILED,
                    "Pipeline output does not match its configured step.",
                )
            if await self.queue.accepted(job):
                return
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
            if job.kind == "purge_material":
                if not await self.queue.accepted(job):
                    raise DomainError(ErrorCode.REVISION_NOT_READY, "Cleanup is not accepted.")
            else:
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
