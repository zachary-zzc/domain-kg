"""Stage canonical target records in scoped, policy-fenced Mongo batches."""

import hashlib

from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.catalog.models import StorageProfile
from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.jobs.models import LeasedJob
from domain_kg.jobs.queue import JobQueue
from domain_kg.operations.capture import BackupCapture
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.restore_canonical import RestoreCanonical
from domain_kg.operations.restore_preparation import PreparedRestore
from domain_kg.operations.restore_stores import RestoreStores
from domain_kg.operations.restore_targets import RestoreTargets
from domain_kg.providers.service import ProviderService
from domain_kg.storage.integrity import COLLECTIONS, CanonicalRecord


class RestoreAuthority:
    def __init__(self, queue: JobQueue, providers: ProviderService) -> None:
        self.queue, self.targets = queue, RestoreTargets(queue)
        self.bindings = RestoreStores(queue, providers.registry)

    async def _guard(
        self, job: LeasedJob, staged: PreparedRestore, session: AsyncClientSession
    ) -> None:
        access = await self.queue.authorize(job, session)
        operation = await OwnerOperations(self.queue).authorize(
            access, await self.queue._current(job, session), session
        )
        target = await self.targets.record(operation, session)
        if await self.queue.database.kgs.find_one(
            {"org_id": str(target.target.org_id), "id": str(target.target.kg_id)}, session=session
        ):
            raise DomainError(
                ErrorCode.VERSION_CONFLICT, "Restore requires a fresh inactive target."
            )
        if (
            target != staged.binding.target
            or target.state not in ("allocated", "preparing", "verified")
            or staged.binding.stores.namespace != target.target
            or staged.binding.stores.authority.namespace != target.target
        ):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Restore target authority differs."
            )
        raw_profile = await self.queue.database.storage_profiles.find_one(
            {"id": str(target.placement.id), "allowed_org_ids": str(job.org_id)}, session=session
        )
        if (
            raw_profile is None
            or StorageProfile.model_validate(
                {key: value for key, value in raw_profile.items() if key != "_id"}
            )
            != target.placement
        ):
            raise DomainError(ErrorCode.VERSION_CONFLICT, "Restore placement changed.")
        seal = await self.targets._backup(operation, session)
        if (
            hashlib.sha256(staged.inventory.manifest.model_dump_json().encode()).hexdigest()
            != seal.manifest.sha256
        ):
            raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Restore package header differs.")
        await BackupCapture(self.queue)._boundary(job, operation.snapshot, session)
        await self.queue.transactions.fence(access, Action.RESTORE, session)
        await self.queue.database.kgs.update_one(
            {"org_id": str(job.org_id), "id": str(job.kg_id)},
            {"$inc": {"restore_authority_epoch": 1}},
            session=session,
        )

    async def stage(self, job: LeasedJob, staged: PreparedRestore) -> None:
        if await self.bindings.approved(job) != (staged.binding.target, staged.binding.embedding):
            raise DomainError(ErrorCode.VERSION_CONFLICT, "Restore configuration changed.")
        mapping = staged.inventory.mapping(staged.binding.target.target)
        records = RestoreCanonical(mapping).records(
            {str(name): rows for name, rows in staged.inventory.records.items()}
        )
        repository = staged.binding.stores.authority
        for start in range(0, len(records), 100):
            batch = records[start : start + 100]

            async def write(
                session: AsyncClientSession, batch: tuple[CanonicalRecord, ...] = batch
            ) -> None:
                await self._guard(job, staged, session)
                for record in batch:
                    collection = repository._database[COLLECTIONS[type(record)]]
                    previous = await collection.find_one(
                        repository._query(record.id), session=session
                    )
                    if previous is None:
                        await repository.insert(record, session=session)
                    elif (
                        type(record).model_validate(
                            {key: value for key, value in previous.items() if key != "_id"}
                        )
                        != record
                    ):
                        raise DomainError(
                            ErrorCode.VERSION_CONFLICT, "Restored canonical record changed."
                        )

            await self.queue.transactions.transaction(write)
        if await repository.reconcile():
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Restored canonical references need repair."
            )
        if await self.bindings.approved(job) != (staged.binding.target, staged.binding.embedding):
            raise DomainError(ErrorCode.VERSION_CONFLICT, "Restore configuration changed.")
