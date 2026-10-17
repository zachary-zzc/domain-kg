"""Authenticate accepted rebuild ancestry without reusing a historical actor or lease."""

from typing import Any
from uuid import UUID

from domain_kg.identity.models import AccessContext, Action, Principal
from domain_kg.jobs.models import JobCommand, LeasedJob
from domain_kg.jobs.queue import JobQueue
from domain_kg.operations.generation_origin import GenerationOrigin, GenerationResult
from domain_kg.operations.generation_targets import GenerationTargets, invalid
from domain_kg.operations.models import OwnerOperation
from domain_kg.revisions.journal import PublicationJournal
from domain_kg.revisions.models import ReadinessReport, RevisionBatch
from domain_kg.revisions.projections import AcceptedProjection, digest


async def sealed_result(
    queue: JobQueue, access: AccessContext, operation_id: UUID
) -> GenerationResult:
    current = await queue.policy.revalidate(access)
    raw = await queue.database.generation_results.find_one(
        {
            "org_id": str(current.org_id),
            "kg_id": {"$in": [str(kg) for kg in current.kg_ids]},
            "id": str(operation_id),
            "state": "sealed",
        }
    )
    if raw is None:
        raise invalid()
    result = GenerationResult.model_validate(
        {key: raw[key] for key in GenerationResult.model_fields}
    )
    ns = {"org_id": str(result.org_id), "kg_id": str(result.kg_id)}
    job = await queue.database.jobs.find_one(
        ns | {"id": str(result.job_id), "kind": "generation", "stage": "ready"}
    )
    raw_operation = await queue.database.owner_operations.find_one(ns | {"id": str(operation_id)})
    if job is None or raw_operation is None:
        raise invalid()
    operation = OwnerOperation.model_validate(
        {key: raw_operation[key] for key in OwnerOperation.model_fields}
    )
    target = await GenerationTargets(queue).record(operation)
    root = await queue.database.publication_manifests.find_one(
        ns
        | {
            "id": str(result.root_manifest_id),
            "state": "accepted",
            "generation_operation_id": str(operation_id),
            "generation": str(result.generation),
            "revision": result.published_revision,
            "job_id": str(result.job_id),
            "attempt_id": str(result.attempt_id),
            "binding_count": result.binding_count,
        }
    )
    if root is None or (
        operation.command.kind != "generation"
        or operation.intent_hash != digest(operation.command.model_dump(mode="json"))
        or operation.job.id != result.job_id
        or operation.actor.model_dump(mode="json") != job["actor"]
        or target.generation != result.generation
        or target.inputs_hash != result.inputs_hash
        or target.binding_count != result.binding_count
        or result.published_revision != operation.snapshot.revision + 1
        or job.get("target_id") != str(operation_id)
        or job.get("attempt_id") != str(result.attempt_id)
        or job.get("accepted_manifest_id") != str(result.root_manifest_id)
        or job.get("accepted_operation_id") != str(operation_id)
        or job.get("accepted_operation_hash") != digest(result.model_dump(mode="json"))
    ):
        raise invalid()
    return result


async def generation_base(
    queue: JobQueue, access: AccessContext, attempt: dict[str, Any]
) -> dict[str, Any]:
    origin = GenerationOrigin.model_validate(attempt["generation_origin"])
    result = await sealed_result(queue, access, origin.id)
    ns = {"org_id": str(origin.org_id), "kg_id": str(origin.kg_id)}
    if (origin.org_id, origin.kg_id, origin.job_id, origin.attempt_id, origin.root_manifest_id) != (
        result.org_id,
        result.kg_id,
        result.job_id,
        result.attempt_id,
        result.root_manifest_id,
    ):
        raise invalid()
    operation_raw = await queue.database.owner_operations.find_one(ns | {"id": str(origin.id)})
    if operation_raw is None:
        raise invalid()
    operation = OwnerOperation.model_validate(
        {key: operation_raw[key] for key in OwnerOperation.model_fields}
    )
    target = await GenerationTargets(queue).record(operation)
    inputs = await GenerationTargets(queue).inputs(target)
    matches = [item for item in inputs if item.id == origin.input_id]
    output = await queue.database.generation_outputs.find_one(
        ns | {"id": str(origin.manifest_id), "operation_id": str(origin.id), "state": "sealed"}
    )
    if len(matches) != 1 or output is None:
        raise invalid()
    item = matches[0]
    binding = AcceptedProjection.model_validate(output["binding"])
    batch = RevisionBatch.model_validate(attempt["batch"])
    actual = JobCommand.model_validate(
        {key: attempt["job"][key] for key in JobCommand.model_fields if key in attempt["job"]}
    )
    root_binding = await queue.database.publication_bindings.find_one(
        ns | {"view_manifest_id": str(result.root_manifest_id), "owner_key": item.owner_key}
    )
    if (
        origin.base != item.base
        or origin.parent_manifest_id != item.parent.manifest_id
        or output.get("origin") != origin.model_dump(mode="json")
        or output.get("input_id") != str(item.id)
        or root_binding is None
        or root_binding["binding"] != output["binding"]
        or (actual.id, actual.target_id, actual.kind) != (origin.job_id, origin.id, "generation")
        or attempt["job"].get("attempt_id") != str(origin.attempt_id)
        or attempt["state"] != "accepted"
        or attempt["id"] != str(origin.manifest_id)
        or any(
            getattr(binding, key) != getattr(batch, key)
            for key in (
                "org_id",
                "kg_id",
                "manifest_id",
                "attempt_id",
                "generation",
                "content_hash",
            )
        )
        or binding.generation != result.generation
        or any(
            getattr(binding, key) != getattr(item.base, key)
            for key in ("source", "curation", "content_hash", "chunk_artifact_id", "import_origin")
        )
    ):
        raise invalid()
    ReadinessReport.model_validate(
        {"batch": attempt["batch"], "receipts": attempt["receipts"], "ready": True}
    )
    base = await PublicationJournal(queue).attempt(access, origin.base.manifest_id)
    if (
        base.get("generation_origin")
        or base.get("maintenance_origin")
        or base["state"] != "accepted"
        or base["embedded_checkpoint"] != attempt["embedded_checkpoint"]
    ):
        raise invalid()
    if any(
        base["batch"][key] != str(getattr(origin.base, key))
        for key in ("org_id", "kg_id", "manifest_id", "attempt_id", "generation", "content_hash")
    ):
        raise invalid()
    await queue.policy.revalidate(access)
    return base


async def artifact_base_binding(
    queue: JobQueue, access: AccessContext, parent: AcceptedProjection
) -> AcceptedProjection:
    value = parent
    for _ in range(8):
        attempt = await PublicationJournal(queue).attempt(access, value.manifest_id)
        if attempt.get("generation_origin"):
            await generation_base(queue, access, attempt)
            value = GenerationOrigin.model_validate(attempt["generation_origin"]).base
        elif attempt.get("maintenance_origin"):
            from domain_kg.operations.maintenance_origin import MaintenanceOrigin
            from domain_kg.operations.maintenance_results import maintenance_base

            await maintenance_base(queue, access, attempt)
            value = MaintenanceOrigin.model_validate(attempt["maintenance_origin"]).base
        else:
            if attempt["state"] != "accepted":
                raise invalid()
            return value
    raise invalid()


async def generation_artifact_job(
    queue: JobQueue, access: AccessContext, attempt: dict[str, Any]
) -> JobCommand:
    base = await generation_base(queue, access, attempt)
    if base.get("restored_origin"):
        from domain_kg.operations.restored_attempts import restored_job

        return await restored_job(queue, access, base)
    return JobCommand.model_validate(
        {key: base["job"][key] for key in JobCommand.model_fields if key in base["job"]}
    )


class GenerationResults:
    def __init__(self, queue: JobQueue) -> None:
        self.queue = queue

    async def accepted(self, job: LeasedJob) -> bool:
        row = await self.queue.database.jobs.find_one(
            self.queue._scope(job)
            | {
                "kind": "generation",
                "stage": "ready",
                "attempt_id": str(job.attempt_id),
                "lease_token": str(job.lease_token),
                "fence": job.fence,
            }
        )
        if row is None:
            return False
        access = await self.queue.policy.resolve_access(
            Principal.model_validate(row["actor"]), [job.kg_id], Action.CONFIGURE, org_id=job.org_id
        )
        result = await sealed_result(self.queue, access, job.target_id)
        ns = {"org_id": str(job.org_id), "kg_id": str(job.kg_id)}
        outputs = (
            await self.queue.database.generation_outputs.find(
                ns | {"operation_id": str(job.target_id), "state": "sealed"}
            )
            .sort("owner_key", 1)
            .limit(1001)
            .to_list()
        )
        if (
            len(outputs) != result.binding_count
            or digest(
                [
                    {"owner_key": value["owner_key"], "binding": value["binding"]}
                    for value in outputs
                ]
            )
            != result.bindings_hash
        ):
            raise invalid()
        for output in outputs:
            await generation_base(
                self.queue,
                access,
                await PublicationJournal(self.queue).attempt(access, UUID(output["id"])),
            )
        await self.queue.policy.revalidate(access, action=Action.CONFIGURE)
        return True
