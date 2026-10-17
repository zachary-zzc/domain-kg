"""Private leased-target bindings reuse bounded pools without fabricating grants."""

from dataclasses import dataclass
from functools import partial

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.jobs.models import LeasedJob
from domain_kg.jobs.queue import JobQueue
from domain_kg.operations.models import RestoreCommand
from domain_kg.operations.restore_targets import RestoreTarget, RestoreTargets
from domain_kg.operations.restore_writes import RestoreWrites
from domain_kg.providers.embeddings import EmbeddingProfile
from domain_kg.providers.profiles import ProviderRegistry, embedding_space_id
from domain_kg.storage.router import RoutedStores


@dataclass(frozen=True)
class RestoreBinding:
    target: RestoreTarget
    stores: RoutedStores
    embedding: EmbeddingProfile


class RestoreStores:
    def __init__(self, queue: JobQueue, providers: ProviderRegistry) -> None:
        self.queue, self.providers = queue, providers
        self.targets = RestoreTargets(queue)

    async def approved(self, job: LeasedJob) -> tuple[RestoreTarget, EmbeddingProfile]:
        from domain_kg.operations.jobs import OwnerOperations

        target = await self.targets.guard(job)
        access = await self.queue.authorize(job)
        operation = await OwnerOperations(self.queue).authorize(
            access, await self.queue._current(job)
        )
        if not isinstance(operation.command, RestoreCommand):
            raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Restore intent differs.")
        # Restored material access starts owner-only. No original provider scope
        # or permission from the package establishes approval for this new KG.
        profile, connection = self.providers.resolve_embedding(
            operation.command.provider_profile_id, target.target, restricted=True
        )
        return target, EmbeddingProfile(
            **target.target.model_dump(),
            id=profile.id,
            revision=profile.revision,
            model=profile.embedding_model,
            dimensions=profile.embedding_dimensions,
            space_id=embedding_space_id(profile, connection),
        )

    async def provision(self, job: LeasedJob) -> RestoreBinding:
        target, embedding = await self.approved(job)
        stores = await self.queue.router._bind_namespace(
            target.target, target.placement, target.generation
        )
        await RestoreWrites(self.queue).run(
            job,
            target,
            "provision",
            partial(self.queue.router._provision_namespace, stores, embedding.dimensions),
        )
        current = await self.approved(job)
        if current != (target, embedding):
            raise DomainError(ErrorCode.VERSION_CONFLICT, "Restore configuration changed.")
        return RestoreBinding(target, stores, embedding)
