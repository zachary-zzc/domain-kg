"""Bounded immutable Mongo artifacts for source-less operations; no fake blob scope."""

import hashlib
from typing import TYPE_CHECKING, Annotated, Self
from uuid import UUID, uuid4

from pydantic import Field, ValidationError, model_validator
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext
from domain_kg.jobs.models import ATTEMPT_STAGES, Checkpoint, LeasedJob, PreparationStage
from domain_kg.models import SHA256, Namespace, PreservedText, ScopedRef

if TYPE_CHECKING:
    from domain_kg.jobs.queue import JobQueue


class OperationArtifact(Namespace):
    id: UUID
    job_id: UUID
    owner: ScopedRef
    stage: PreparationStage
    input_hash: SHA256
    output_hash: SHA256
    body: Annotated[PreservedText, Field(min_length=1, max_length=1024 * 1024)]

    @model_validator(mode="after")
    def sealed_owner(self) -> Self:
        if (self.org_id, self.kg_id) != (self.owner.org_id, self.owner.kg_id):
            raise ValueError("operation artifacts require an exact namespace")
        if (
            len(self.body.encode()) > 1024 * 1024
            or hashlib.sha256(self.body.encode()).hexdigest() != self.output_hash
        ):
            raise ValueError("operation artifact checksum or bound differs")
        return self


async def store_operation_artifact(
    queue: "JobQueue", job: LeasedJob, stage: PreparationStage, input_hash: str, data: bytes
) -> Checkpoint:
    if job.kind != "curate":
        raise inaccessible_reference()
    await queue.authorize(job)
    if len(data) > 1024 * 1024:
        raise DomainError(ErrorCode.QUOTA_EXCEEDED, "The operation artifact exceeds its bound.")
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        raise DomainError(
            ErrorCode.VALIDATION_ERROR, "Operation artifacts require UTF-8."
        ) from None
    artifact = OperationArtifact(
        org_id=job.org_id,
        kg_id=job.kg_id,
        id=uuid4(),
        job_id=job.id,
        owner=ScopedRef(org_id=job.org_id, kg_id=job.kg_id, id=job.target_id),
        stage=stage,
        input_hash=input_hash,
        output_hash=hashlib.sha256(data).hexdigest(),
        body=text,
    )

    async def save(session: AsyncClientSession) -> None:
        await queue.authorize(job, session)
        await queue.database.operation_artifacts.insert_one(
            artifact.model_dump(mode="json"), session=session
        )
        changed = await queue.database.jobs.update_one(
            queue._active(job), {"$inc": {"artifact_epoch": 1}}, session=session
        )
        if not changed.matched_count:
            raise DomainError(ErrorCode.VERSION_CONFLICT, "The operation lease changed.")

    await queue.transactions.transaction(save)
    return Checkpoint(
        org_id=job.org_id,
        kg_id=job.kg_id,
        job_id=job.id,
        stage=stage,
        input_hash=input_hash,
        artifact_id=artifact.id,
        output_hash=artifact.output_hash,
        attempt_id=job.attempt_id if stage in ATTEMPT_STAGES else None,
    )


async def read_operation_artifact(
    queue: "JobQueue",
    access: AccessContext,
    owner: ScopedRef,
    checkpoint: Checkpoint,
    *,
    job: LeasedJob | None = None,
    session: AsyncClientSession | None = None,
) -> bytes:
    from domain_kg.graph.curation import authorize_curation

    if (checkpoint.org_id, checkpoint.kg_id) != (
        owner.org_id,
        owner.kg_id,
    ) or owner.org_id != access.org_id:
        raise inaccessible_reference()
    if job is not None:
        if (job.org_id, job.kg_id, job.id, job.target_id, job.kind) != (
            owner.org_id,
            owner.kg_id,
            checkpoint.job_id,
            owner.id,
            "curate",
        ):
            raise inaccessible_reference()
        await queue.authorize(job, session)
    record = await authorize_curation(queue, access, owner.id, kg_id=owner.kg_id, session=session)
    ns = {"org_id": str(owner.org_id), "kg_id": str(owner.kg_id)}
    raw = await queue.database.operation_artifacts.find_one(
        ns
        | {
            "id": str(checkpoint.artifact_id),
            "owner": owner.model_dump(mode="json"),
            "job_id": str(checkpoint.job_id),
            "stage": checkpoint.stage,
            "input_hash": checkpoint.input_hash,
        },
        session=session,
    )
    if raw is None:
        raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "The operation artifact is missing.")
    try:
        artifact = OperationArtifact.model_validate(
            {key: value for key, value in raw.items() if key != "_id"}
        )
    except ValidationError:
        raise DomainError(
            ErrorCode.PROJECTION_INCONSISTENT, "The operation artifact is invalid."
        ) from None
    origin = await queue.database.jobs.find_one(
        ns | {"id": str(checkpoint.job_id), "target_id": str(owner.id), "kind": "curate"},
        session=session,
    )
    if (
        artifact.output_hash != checkpoint.output_hash
        or origin is None
        or origin["actor"] != record["actor"]
        or record["job_id"] != str(checkpoint.job_id)
    ):
        raise DomainError(
            ErrorCode.PROJECTION_INCONSISTENT, "The operation artifact owner differs."
        )
    if job is not None:
        await queue.authorize(job, session)
    else:
        await authorize_curation(queue, access, owner.id, kg_id=owner.kg_id, session=session)
    return artifact.body.encode()
