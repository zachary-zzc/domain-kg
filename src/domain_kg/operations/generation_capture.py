"""Current owner rebuild pins; private evidence reads never relax ordinary visibility."""

from datetime import UTC
from typing import Self
from uuid import UUID, uuid5

from pydantic import model_validator
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.models import LeasedJob
from domain_kg.jobs.queue import JobQueue, now
from domain_kg.models import FrozenModel, SourceVersion
from domain_kg.operations.capture import BackupCapture
from domain_kg.operations.generation_targets import (
    GenerationInput,
    GenerationTarget,
    GenerationTargets,
    invalid,
    material_hash,
)
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import OwnerOperation


def pin_id(job: LeasedJob) -> UUID:
    return uuid5(job.id, f"generation-capture/{job.attempt_id}")


class GenerationReadPermit(FrozenModel):
    job: LeasedJob
    source: SourceVersion
    pin_id: UUID

    @model_validator(mode="after")
    def exact_owner(self) -> Self:
        if (
            self.job.kind != "generation"
            or self.pin_id != pin_id(self.job)
            or ((self.source.org_id, self.source.kg_id) != (self.job.org_id, self.job.kg_id))
        ):
            raise ValueError("rebuild reads require the exact actual generation job and source")
        return self


class GenerationCapture:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database = queue, queue.database
        self.targets = GenerationTargets(queue)

    @staticmethod
    def pin_key(job: LeasedJob) -> dict[str, str]:
        return {
            "org_id": str(job.org_id),
            "kg_id": str(job.kg_id),
            "id": str(pin_id(job)),
            "kind": "generation_capture",
            "job_id": str(job.id),
            "operation_id": str(job.target_id),
            "attempt_id": str(job.attempt_id),
        }

    async def context(
        self, job: LeasedJob, session: AsyncClientSession | None = None
    ) -> tuple[AccessContext, OwnerOperation, GenerationTarget]:
        if job.kind != "generation":
            raise inaccessible_reference()
        access = await self.queue.authorize(job, session)
        raw = await self.queue._current(job, session)
        operation = await OwnerOperations(self.queue).authorize(access, raw, session)
        target = await self.targets.record(operation, session)
        await BackupCapture(self.queue)._boundary(job, operation.snapshot, session)
        profile, config_hash = await self.targets.configuration(access, operation, session)
        if profile != target.profile or config_hash != target.configuration_hash:
            raise DomainError(ErrorCode.VERSION_CONFLICT, "Rebuild configuration changed.")
        return access, operation, target

    async def guard(
        self, job: LeasedJob, session: AsyncClientSession | None = None
    ) -> tuple[AccessContext, OwnerOperation, GenerationTarget]:
        access, operation, target = await self.context(job, session)
        pin = await self.database.read_pins.find_one(
            self.pin_key(job) | {"expires_at": {"$gt": now()}}, session=session
        )
        if pin is None:
            raise DomainError(ErrorCode.REVISION_NOT_READY, "The live rebuild pin is missing.")
        if pin.get("inputs_hash") != target.inputs_hash or pin.get(
            "snapshot"
        ) != operation.snapshot.model_dump(mode="json"):
            raise invalid()
        if pin["policy_revision"] != access.policy_revision:
            raise DomainError(ErrorCode.POLICY_CHANGED, "Policy changed during rebuild.")
        return access, operation, target

    async def check_input(
        self,
        target: GenerationTarget,
        item: GenerationInput,
        session: AsyncClientSession | None = None,
    ) -> None:
        if (item.org_id, item.kg_id, item.operation_id) != (target.org_id, target.kg_id, target.id):
            raise invalid()
        ns = {"org_id": str(target.org_id), "kg_id": str(target.kg_id)}
        row = await self.database.publication_bindings.find_one(
            ns | {"view_manifest_id": str(target.parent_manifest_id), "owner_key": item.owner_key},
            session=session,
        )
        if row is None or row["binding"] != item.parent.model_dump(mode="json"):
            raise invalid()
        if item.parent.source:
            material = await self.database.material_access.find_one(
                ns | {"id": str(item.parent.source.material_id)}, session=session
            )
            if material is None or material_hash(material) != item.source_access_hash:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "A rebuild source fence changed.")
        for dependency in item.dependencies:
            material = await self.database.material_access.find_one(
                ns | {"id": str(dependency.id)}, session=session
            )
            if material is None or material_hash(material) != dependency.access_hash:
                raise DomainError(
                    ErrorCode.VERSION_CONFLICT, "A curation dependency fence changed."
                )

    async def check_all(
        self, target: GenerationTarget, session: AsyncClientSession | None = None
    ) -> tuple[GenerationInput, ...]:
        inputs = await self.targets.inputs(target, session)
        for item in inputs:
            await self.check_input(target, item, session)
        return inputs

    async def start(self, job: LeasedJob) -> tuple[GenerationInput, ...]:
        await self.database.read_pins.create_index("expires_at", expireAfterSeconds=0)
        await self.database.read_pins.create_index(
            [("org_id", 1), ("kg_id", 1), ("id", 1)],
            unique=True,
            partialFilterExpression={"kind": "generation_capture"},
            name="generation_capture_identity",
        )

        async def protect(session: AsyncClientSession) -> tuple[GenerationInput, ...]:
            access, operation, target = await self.context(job, session)
            await self.queue.transactions.fence(access, Action.CONFIGURE, session)
            inputs = await self.check_all(target, session)
            materials = sorted(
                {str(item.parent.source.material_id) for item in inputs if item.parent.source}
                | {str(dependency.id) for item in inputs for dependency in item.dependencies}
            )
            # Serialize pin admission with purge admission/completion on each source.
            if materials:
                await self.database.material_access.update_many(
                    {"org_id": str(job.org_id), "kg_id": str(job.kg_id), "id": {"$in": materials}},
                    {"$inc": {"generation_pin_epoch": 1}},
                    session=session,
                )
            raw = await self.queue._current(job, session)
            await self.database.read_pins.update_one(
                self.pin_key(job),
                {
                    "$setOnInsert": {
                        **self.pin_key(job),
                        "inputs_hash": target.inputs_hash,
                        "snapshot": operation.snapshot.model_dump(mode="json"),
                        "policy_revision": access.policy_revision,
                        "material_ids": materials,
                        "expires_at": raw["lease_until"].replace(tzinfo=UTC),
                    }
                },
                upsert=True,
                session=session,
            )
            await self.guard(job, session)
            await self.database.kgs.update_one(
                {"org_id": str(job.org_id), "id": str(job.kg_id)},
                {"$inc": {"generation_capture_epoch": 1}},
                session=session,
            )
            return inputs

        return await self.queue.transactions.transaction(protect)

    async def source_permit(self, job: LeasedJob, source: SourceVersion) -> GenerationReadPermit:
        _, _, target = await self.guard(job)
        rows = (
            await self.database.generation_inputs.find(
                {
                    "org_id": str(job.org_id),
                    "kg_id": str(job.kg_id),
                    "operation_id": str(job.target_id),
                    "parent.source": source.model_dump(mode="json"),
                }
            )
            .limit(2)
            .to_list()
        )
        if len(rows) != 1:
            raise inaccessible_reference()
        item = GenerationInput.model_validate(
            {key: rows[0][key] for key in GenerationInput.model_fields if key in rows[0]}
        )
        await self.check_input(target, item)
        return GenerationReadPermit(job=job, source=source, pin_id=pin_id(job))

    async def release(self, job: LeasedJob) -> None:
        await self.database.read_pins.delete_one(self.pin_key(job))


async def authorize_generation_read(
    queue: JobQueue, access: AccessContext, source: SourceVersion, permit: GenerationReadPermit
) -> None:
    permit = GenerationReadPermit.model_validate(permit)
    if (
        permit.source != source
        or access.action != Action.CONFIGURE
        or access.org_id != source.org_id
        or source.kg_id not in access.kg_ids
    ):
        raise inaccessible_reference()
    capture = GenerationCapture(queue)
    current, _, _ = await capture.guard(permit.job)
    if current.principal != access.principal:
        raise inaccessible_reference()
    await capture.source_permit(permit.job, source)
