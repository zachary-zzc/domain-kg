"""Read sealed curation data under a real owner job, even after projection loss."""

from dataclasses import dataclass, field

from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import inaccessible_reference
from domain_kg.graph.curation import authorize_curation
from domain_kg.identity.models import AccessContext
from domain_kg.jobs.models import LeasedJob
from domain_kg.jobs.queue import JobQueue
from domain_kg.operations.capture import BackupCapture
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.maintenance_targets import MaintenanceTarget, MaintenanceTargets
from domain_kg.operations.models import OwnerOperation
from domain_kg.revisions.curation_publication import CurationCoordinator, directive
from domain_kg.revisions.models import ProjectionReceipt, RevisionBatch
from domain_kg.revisions.projections import CurationEmbeddedDocument
from domain_kg.revisions.publication import accepted_document


@dataclass(frozen=True)
class PreparedMaintenance:
    target: MaintenanceTarget
    operation: OwnerOperation
    document: CurationEmbeddedDocument = field(repr=False)
    parent_batch: RevisionBatch
    parent_receipts: tuple[ProjectionReceipt, ...]


class MaintenancePreparation:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database = queue, queue.database
        self.targets = MaintenanceTargets(queue)

    async def guard(
        self, job: LeasedJob, session: AsyncClientSession | None = None
    ) -> tuple[AccessContext, OwnerOperation, MaintenanceTarget]:
        if job.kind != "maintenance":
            raise inaccessible_reference()
        access = await self.queue.authorize(job, session)
        raw = await self.queue._current(job, session)
        operation = await OwnerOperations(self.queue).authorize(access, raw, session)
        target = await self.targets.record(operation, session)
        await BackupCapture(self.queue)._boundary(job, operation.snapshot, session)
        if target.parent.curation is None:
            raise self.targets.invalid()
        record = await authorize_curation(
            self.queue, access, target.parent.curation.id, kg_id=job.kg_id, session=session
        )
        if (
            record["state"] != "accepted"
            or CurationCoordinator.owner_key(record) != target.owner_key
        ):
            raise self.targets.invalid()
        parent = await self.database.publication_bindings.find_one(
            {
                "org_id": str(job.org_id),
                "kg_id": str(job.kg_id),
                "view_manifest_id": str(target.view_manifest_id),
                "owner_key": target.owner_key,
            },
            session=session,
        )
        if parent is None or parent["binding"] != target.parent.model_dump(mode="json"):
            raise self.targets.invalid()
        return access, operation, target

    async def prepare(self, job: LeasedJob) -> PreparedMaintenance:
        access, operation, target = await self.guard(job)
        document, batch, receipts = await accepted_document(self.queue, access, target.parent)
        if not isinstance(document, CurationEmbeddedDocument) or target.parent.curation is None:
            raise self.targets.invalid()
        record = await authorize_curation(
            self.queue, access, target.parent.curation.id, kg_id=job.kg_id
        )
        if document.directive != directive(record):
            raise self.targets.invalid()
        await self.guard(job)
        return PreparedMaintenance(target, operation, document, batch, receipts)
