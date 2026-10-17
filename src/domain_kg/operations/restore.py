"""Serial restore orchestration; only verified activation makes the target available."""

from domain_kg.errors import inaccessible_reference
from domain_kg.jobs.models import LeasedJob
from domain_kg.jobs.queue import JobQueue
from domain_kg.operations.restore_activation import RestoreActivation
from domain_kg.operations.restore_authority import RestoreAuthority
from domain_kg.operations.restore_control import RestoreControl
from domain_kg.operations.restore_human_history import RestoreHumanHistory
from domain_kg.operations.restore_preparation import RestorePreparation
from domain_kg.operations.restore_projections import RestoreProjections
from domain_kg.operations.restore_results import RestoreResults
from domain_kg.providers.service import ProviderService


class RestoreService:
    def __init__(self, queue: JobQueue, providers: ProviderService) -> None:
        self.queue, self.providers = queue, providers

    async def run(self, job: LeasedJob) -> None:
        if job.kind != "restore":
            raise inaccessible_reference()
        if await RestoreResults(self.queue).accepted(job):
            return
        staged = await RestorePreparation(self.queue, self.providers).prepare(job)
        await RestoreAuthority(self.queue, self.providers).stage(job, staged)
        await RestoreControl(self.queue, self.providers).stage(job, staged)
        await RestoreHumanHistory(self.queue, self.providers).stage(job, staged)
        parts = await RestoreProjections(self.queue, self.providers).stage(job, staged)
        await RestoreActivation(self.queue, self.providers).activate(job, staged, parts)
