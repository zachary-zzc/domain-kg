"""An owner maintenance operation pins one accepted curation and its stable owner key."""

from typing import Self
from uuid import UUID

from pydantic import model_validator
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.curation import authorize_curation
from domain_kg.identity.models import AccessContext
from domain_kg.jobs.queue import JobQueue
from domain_kg.models import ScopedRef
from domain_kg.operations.models import MaintenanceCommand, OwnerOperation
from domain_kg.revisions.curation_publication import CurationCoordinator
from domain_kg.revisions.projections import AcceptedProjection


class MaintenanceTarget(ScopedRef):
    job: ScopedRef
    view_manifest_id: UUID
    parent: AcceptedProjection
    base: AcceptedProjection
    owner_key: str

    @model_validator(mode="after")
    def accepted_curation_owner(self) -> Self:
        if (
            self.parent.curation is None
            or self.parent.source is not None
            or self.base.curation != self.parent.curation
            or self.base.generation != self.parent.generation
            or self.base.content_hash != self.parent.content_hash
            or any(
                (value.org_id, value.kg_id) != (self.org_id, self.kg_id)
                for value in (self.job, self.parent, self.base)
            )
        ):
            raise ValueError("maintenance must pin a same-KG accepted curation")
        return self


class MaintenanceTargets:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database = queue, queue.database

    async def initialize(self) -> None:
        await self.database.maintenance_targets.create_index(
            [("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True
        )

    @staticmethod
    def invalid() -> DomainError:
        return DomainError(
            ErrorCode.PROJECTION_INCONSISTENT, "Maintenance parent reference differs."
        )

    async def allocate(
        self, access: AccessContext, operation: OwnerOperation, session: AsyncClientSession
    ) -> MaintenanceTarget:
        command = operation.command
        if not isinstance(command, MaintenanceCommand) or operation.actor.kind != "human":
            raise inaccessible_reference()
        if operation.snapshot.manifest_id is None:
            raise inaccessible_reference()
        record = await authorize_curation(
            self.queue, access, command.curation_id, kg_id=command.kg_id, session=session
        )
        if record["state"] != "accepted":
            raise inaccessible_reference()
        ns = {"org_id": str(operation.org_id), "kg_id": str(operation.kg_id)}
        rows = (
            await self.database.publication_bindings.find(
                ns
                | {
                    "view_manifest_id": str(operation.snapshot.manifest_id),
                    "binding.curation.id": str(command.curation_id),
                },
                session=session,
            )
            .limit(2)
            .to_list()
        )
        if len(rows) != 1:
            raise inaccessible_reference()
        parent = AcceptedProjection.model_validate(rows[0]["binding"])
        owner_key = CurationCoordinator.owner_key(record)
        if (parent.org_id, parent.kg_id, parent.generation, rows[0]["owner_key"]) != (
            operation.org_id,
            operation.kg_id,
            operation.snapshot.generation,
            owner_key,
        ):
            raise self.invalid()
        attempt = await self.database.projection_attempts.find_one(
            ns | {"id": str(parent.manifest_id), "state": "accepted"}, session=session
        )
        if (
            attempt is None
            or attempt.get("curation")
            != (parent.curation.model_dump(mode="json") if parent.curation else None)
            or any(
                attempt["batch"].get(field) != str(getattr(parent, field))
                for field in (
                    "org_id",
                    "kg_id",
                    "manifest_id",
                    "attempt_id",
                    "generation",
                    "content_hash",
                )
            )
        ):
            raise self.invalid()
        base = parent
        if attempt.get("maintenance_origin"):
            from domain_kg.operations.maintenance_origin import MaintenanceOrigin
            from domain_kg.operations.maintenance_results import maintenance_base

            await maintenance_base(self.queue, access, attempt)
            base = MaintenanceOrigin.model_validate(attempt["maintenance_origin"]).base
        target = MaintenanceTarget(
            org_id=operation.org_id,
            kg_id=operation.kg_id,
            id=operation.id,
            job=operation.job,
            view_manifest_id=operation.snapshot.manifest_id,
            parent=parent,
            base=base,
            owner_key=owner_key,
        )
        await self.database.maintenance_targets.insert_one(
            target.model_dump(mode="json"), session=session
        )
        return target

    async def record(
        self, operation: OwnerOperation, session: AsyncClientSession | None = None
    ) -> MaintenanceTarget:
        command = operation.command
        if not isinstance(command, MaintenanceCommand):
            raise inaccessible_reference()
        raw = await self.database.maintenance_targets.find_one(
            {
                "org_id": str(operation.org_id),
                "kg_id": str(operation.kg_id),
                "id": str(operation.id),
            },
            session=session,
        )
        if raw is None:
            raise self.invalid()
        target = MaintenanceTarget.model_validate(
            {key: value for key, value in raw.items() if key != "_id"}
        )
        if (
            target.job != operation.job
            or target.view_manifest_id != operation.snapshot.manifest_id
            or (
                target.parent.curation is None
                or target.parent.curation.id != command.curation_id
                or target.parent.generation != operation.snapshot.generation
            )
        ):
            raise self.invalid()
        return target
