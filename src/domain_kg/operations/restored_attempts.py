"""Accepted restore origin authenticates history without copying an active lease."""

from typing import Any

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import AccessContext
from domain_kg.jobs.models import JobCommand
from domain_kg.jobs.queue import JobQueue
from domain_kg.operations.restore_origin import RestoredProjectionOrigin
from domain_kg.revisions.models import RevisionBatch


async def restored_job(
    queue: JobQueue, access: AccessContext, attempt: dict[str, Any]
) -> JobCommand:
    current = await queue.policy.revalidate(access)
    origin = RestoredProjectionOrigin.model_validate(attempt["restored_origin"])
    batch = RevisionBatch.model_validate(attempt["batch"])
    if (
        origin.org_id != current.org_id
        or origin.kg_id not in current.kg_ids
        or (
            origin.org_id,
            origin.kg_id,
            origin.manifest_id,
            origin.projection_attempt_id,
            origin.generation,
            origin.content_hash,
        )
        != (
            batch.org_id,
            batch.kg_id,
            batch.manifest_id,
            batch.attempt_id,
            batch.generation,
            batch.content_hash,
        )
    ):
        raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Restored attempt origin differs.")
    if attempt.get("source") != (
        origin.source.model_dump(mode="json") if origin.source else None
    ) or (
        attempt.get("curation")
        != (origin.curation.model_dump(mode="json") if origin.curation else None)
    ):
        raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Restored attempt owner differs.")
    header = await queue.database.publication_manifests.find_one(
        {
            "org_id": str(origin.org_id),
            "kg_id": str(origin.kg_id),
            "id": str(origin.root_manifest_id),
            "state": "accepted",
            "restore_operation_id": str(origin.operation_id),
            "generation": str(origin.generation),
        }
    )
    if header is None:
        raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Accepted restore origin is missing.")
    job = JobCommand.model_validate(attempt["job"])
    if (job.org_id, job.kg_id) != (origin.org_id, origin.kg_id):
        raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Restored job namespace differs.")
    await queue.policy.revalidate(current)
    return job
