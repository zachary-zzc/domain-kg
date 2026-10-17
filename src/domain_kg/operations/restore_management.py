"""Owner-facing restore intent and progress omit storage connections and old grants."""

from typing import Literal, Self
from uuid import UUID

from pydantic import model_validator

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.models import JobReceipt, JobStage
from domain_kg.models import Namespace
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import RestoreCommand
from domain_kg.operations.restore_targets import RestoreTargets
from domain_kg.providers.profiles import ProviderRegistry


class RestoreInfo(Namespace):
    job_id: UUID
    target: Namespace
    placement_id: UUID
    provider_profile_id: UUID
    state: Literal["allocated", "preparing", "verified", "activated", "abandoned"]
    stage: JobStage
    provider_approved: bool

    @model_validator(mode="after")
    def fresh_namespace(self) -> Self:
        if self.target.org_id != self.org_id or self.target.kg_id == self.kg_id:
            raise ValueError("a restore requires a fresh same-organization namespace")
        return self


class RestoreManagement:
    def __init__(self, operations: OwnerOperations, providers: ProviderRegistry) -> None:
        self.operations, self.providers, self.queue = operations, providers, operations.queue

    async def submit(self, access: AccessContext, command: RestoreCommand) -> JobReceipt:
        command = RestoreCommand.model_validate(command)
        current = await self.queue.policy.revalidate(access, action=Action.RESTORE)
        if command.kg_id not in current.kg_ids:
            raise inaccessible_reference()
        profile = self.providers.profiles.get(command.provider_profile_id)
        if profile is None or not profile.data_policy_approved:
            raise DomainError(ErrorCode.PROVIDER_UNAVAILABLE, "An approved provider is required.")
        if profile.embedding_dimensions != self.queue.router.dimensions:
            raise DomainError(
                ErrorCode.VERSION_CONFLICT, "Embedding dimensions require a new generation."
            )
        # This does not approve the newly allocated namespace. Its provider scope is
        # checked independently by the restore worker before any target model I/O.
        return await self.operations.submit(current, command)

    async def get(self, access: AccessContext, kg: UUID, job_id: UUID) -> RestoreInfo:
        current = await self.queue.policy.revalidate(access, action=Action.RESTORE)
        if kg not in current.kg_ids:
            raise inaccessible_reference()
        receipt = await self.queue.get(current, job_id)
        target = await RestoreTargets(self.queue).get(current, kg, job_id)
        raw = await self.queue.database.jobs.find_one(
            {"org_id": str(current.org_id), "kg_id": str(kg), "id": str(job_id), "kind": "restore"}
        )
        if raw is None:
            raise inaccessible_reference()
        operation = await self.operations.authorize(current, raw)
        if not isinstance(operation.command, RestoreCommand):
            raise inaccessible_reference()
        approved = False
        try:
            profile, _ = self.providers.resolve_embedding(
                operation.command.provider_profile_id, target.target, restricted=True
            )
            approved = profile.embedding_dimensions == self.queue.router.dimensions
        except DomainError:
            pass
        result = RestoreInfo(
            org_id=current.org_id,
            kg_id=kg,
            job_id=job_id,
            target=target.target,
            placement_id=target.placement.id,
            provider_profile_id=operation.command.provider_profile_id,
            state=target.state,
            stage=receipt.stage,
            provider_approved=approved,
        )
        await self.queue.policy.revalidate(current, action=Action.RESTORE)
        return result
