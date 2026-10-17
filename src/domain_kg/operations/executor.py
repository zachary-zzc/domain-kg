"""One serial owner worker dispatches only implemented, model-free operations."""

from domain_kg.errors import inaccessible_reference
from domain_kg.jobs.models import LeasedJob
from domain_kg.jobs.queue import JobQueue
from domain_kg.operations.backup import BackupService
from domain_kg.operations.export import ExportService


class OwnerJobExecutor:
    def __init__(self, queue: JobQueue) -> None:
        self.backups, self.exports = BackupService(queue), ExportService(queue)

    async def run(self, job: LeasedJob) -> None:
        if job.kind == "backup":
            await self.backups.run(job)
        elif job.kind == "export":
            await self.exports.run(job)
        else:
            raise inaccessible_reference()
