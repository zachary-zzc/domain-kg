"""Narrow current-policy authorization for reading retained data during restoration."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

from pydantic import ValidationError
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.models import Checkpoint, JobCommand, LeasedJob
from domain_kg.jobs.queue import JobQueue
from domain_kg.materials.models import (
    BlobRef,
    MaterialReadPermit,
    ReprocessReadPermit,
    RestoreReadPermit,
)
from domain_kg.models import SourceVersion
from domain_kg.revisions.projections import AcceptedProjection


async def authorize_restore_read(
    queue: JobQueue,
    access: AccessContext,
    source: SourceVersion,
    permit: MaterialReadPermit,
    session: AsyncClientSession | None = None,
) -> None:
    unpublished = isinstance(permit, ReprocessReadPermit)
    current = await queue.policy.revalidate(access, action=Action.REPLACE, session=session)
    if (
        (permit.org_id, permit.kg_id) != (current.org_id, source.kg_id)
        or source.org_id != current.org_id
        or permit.source != source
        or source.kg_id not in current.kg_ids
    ):
        raise inaccessible_reference()
    namespace = {"org_id": str(source.org_id), "kg_id": str(source.kg_id)}
    intent = await queue.database.material_commands.find_one(
        namespace | {"id": str(permit.command_id), "operation": "restore", "state": "pending"},
        session=session,
    )
    if (
        intent is None
        or intent.get("source") != source.model_dump(mode="json")
        or intent.get("expected_revision") != permit.expected_revision
        or bool(intent.get("unpublished", False)) != unpublished
        or any(
            intent.get("actor", {}).get(key) != getattr(current.principal, key)
            for key in ("issuer", "subject", "kind")
        )
    ):
        raise inaccessible_reference()
    now = datetime.now(UTC)
    if permit.job_id is not None:
        job = await queue.database.jobs.find_one(
            namespace
            | {
                "id": str(permit.job_id),
                "kind": "reprocess_material" if unpublished else "restore_material",
                "restore_intent_id": str(permit.command_id),
                "fence": permit.job_fence,
                "lease_token": str(permit.job_lease_token),
                "lease_until": {"$gt": now},
                "stage": {"$nin": ["ready", "ready_with_warnings", "cancelled", "failed"]},
            },
            session=session,
        )
        if (
            job is None
            or intent.get("job_id") != str(permit.job_id)
            or job["target_id"] != str(source.version_id)
            or job["material_id"] != str(source.material_id)
            or any(
                job["actor"].get(key) != getattr(current.principal, key)
                for key in ("issuer", "subject", "kind")
            )
        ):
            raise inaccessible_reference()
    elif intent["expires_at"].replace(tzinfo=UTC) <= now:
        raise DomainError(ErrorCode.VERSION_CONFLICT, "The restoration read window expired.")
    material = await queue.policy._material_acl_record(
        current, source.kg_id, source.material_id, session=session
    )
    if material is None:
        raise inaccessible_reference()
    if (
        material["revision"] != permit.expected_revision
        or material.get("active_version_id") != (None if unpublished else str(source.version_id))
        or not (material.get("tombstoned") or material.get("excluded"))
    ):
        raise DomainError(ErrorCode.VERSION_CONFLICT, "The restoration target changed.")
    if material.get("tombstoned") and (
        not material.get("purge_after") or material["purge_after"].replace(tzinfo=UTC) <= now
    ):
        raise DomainError(ErrorCode.VERSION_CONFLICT, "The source is outside live retention.")
    kg = await queue.database.kgs.find_one(
        {
            "org_id": namespace["org_id"],
            "id": namespace["kg_id"],
            "published_manifest_id": str(permit.view_manifest_id)
            if permit.view_manifest_id
            else None,
            "generation": str(permit.generation),
            "visibility_revision": permit.visibility_revision,
        },
        session=session,
    )
    if unpublished:
        if kg is None:
            raise DomainError(ErrorCode.POLICY_CHANGED, "The restoration publication changed.")
        from domain_kg.materials.reprocessing import unpublished_source
        from domain_kg.models import ScopedRef

        original = await unpublished_source(
            queue,
            current,
            ScopedRef(org_id=source.org_id, kg_id=source.kg_id, id=source.material_id),
            session,
        )
        if original != source:
            raise inaccessible_reference()
        return
    assert isinstance(permit, RestoreReadPermit)
    binding = await queue.database.publication_bindings.find_one(
        namespace
        | {
            "view_manifest_id": str(permit.view_manifest_id),
            "owner_key": f"source:{source.material_id}",
            "binding.manifest_id": str(permit.projection_manifest_id),
        },
        session=session,
    )
    if kg is None or binding is None:
        raise DomainError(ErrorCode.POLICY_CHANGED, "The retained publication changed.")
    accepted = AcceptedProjection.model_validate(binding["binding"])
    if accepted.source != source or accepted.generation != permit.generation:
        raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Retained source ownership differs.")


async def authorize_restore_job(
    queue: JobQueue,
    access: AccessContext,
    command: JobCommand,
    material: UUID,
    session: AsyncClientSession | None = None,
) -> dict[str, Any]:
    current = await queue.policy.revalidate(access, action=Action.REPLACE, session=session)
    if (
        command.kind not in ("restore_material", "reprocess_material")
        or command.restore_intent_id is None
        or command.org_id != current.org_id
        or command.kg_id not in current.kg_ids
    ):
        raise inaccessible_reference()
    intent = await queue.database.material_commands.find_one(
        {
            "org_id": str(command.org_id),
            "kg_id": str(command.kg_id),
            "id": str(command.restore_intent_id),
            "operation": "restore",
            "state": "pending",
        },
        session=session,
    )
    unpublished = command.kind == "reprocess_material"
    if (
        intent is None
        or bool(intent.get("unpublished", False)) != unpublished
        or command.id
        != uuid5(
            command.restore_intent_id, "reprocess-material" if unpublished else "restore-material"
        )
    ):
        raise inaccessible_reference()
    source = SourceVersion.model_validate(intent["source"])
    if (
        (source.org_id, source.kg_id, source.material_id, source.version_id)
        != (command.org_id, command.kg_id, material, command.target_id)
        or any(
            intent["actor"].get(key) != getattr(current.principal, key)
            for key in ("issuer", "subject", "kind")
        )
        or intent.get("job_id") not in (None, str(command.id))
    ):
        raise inaccessible_reference()
    row = await queue.policy._material_acl_record(current, command.kg_id, material, session=session)
    if row is None:
        raise inaccessible_reference()
    if (
        row["revision"] != intent["expected_revision"]
        or row.get("active_version_id") != (None if unpublished else str(source.version_id))
        or not (row.get("excluded") or row.get("tombstoned"))
    ):
        raise DomainError(ErrorCode.VERSION_CONFLICT, "The restoration target changed.")
    if row.get("tombstoned") and (
        not row.get("purge_after") or row["purge_after"].replace(tzinfo=UTC) <= datetime.now(UTC)
    ):
        raise DomainError(ErrorCode.VERSION_CONFLICT, "The source is outside live retention.")
    return dict(intent)


async def restoration_permit(
    queue: JobQueue, access: AccessContext, job: LeasedJob
) -> MaterialReadPermit | None:
    if job.kind not in ("restore_material", "reprocess_material"):
        return None
    if job.restore_intent_id is None:
        raise inaccessible_reference()
    await queue.authorize(job)
    raw = await queue._current(job)
    intent = await authorize_restore_job(queue, access, job, UUID(raw["material_id"]))
    source = SourceVersion.model_validate(intent["source"])
    from domain_kg.revisions.journal import PublicationJournal

    view = await PublicationJournal(queue).load_metadata(access, job.kg_id)
    binding = next((item for item in view.projections if item.source == source), None)
    if job.kind == "restore_material" and (view.manifest_id is None or binding is None):
        raise DomainError(
            ErrorCode.REVISION_NOT_READY, "The retained source binding is unavailable."
        )
    kg = await queue.database.kgs.find_one(
        {
            "org_id": str(job.org_id),
            "id": str(job.kg_id),
            "published_manifest_id": str(view.manifest_id) if view.manifest_id else None,
            "generation": str(view.generation),
        }
    )
    if kg is None:
        raise DomainError(ErrorCode.POLICY_CHANGED, "The retained publication changed.")
    common = dict(
        org_id=job.org_id,
        kg_id=job.kg_id,
        command_id=job.restore_intent_id,
        source=source,
        expected_revision=intent["expected_revision"],
        view_manifest_id=view.manifest_id,
        generation=view.generation,
        visibility_revision=kg["visibility_revision"],
        job_id=job.id,
        job_fence=job.fence,
        job_lease_token=job.lease_token,
    )
    permit: MaterialReadPermit
    if job.kind == "reprocess_material":
        permit = ReprocessReadPermit(**common)
    else:
        if binding is None or view.manifest_id is None:
            raise inaccessible_reference()
        permit = RestoreReadPermit(**common, projection_manifest_id=binding.manifest_id)
    await authorize_restore_read(queue, access, source, permit)
    return permit


async def restore_embedding_artifact(
    queue: JobQueue, job: LeasedJob, *, input_hash: str
) -> Checkpoint:
    import hashlib

    from domain_kg.revisions.journal import PublicationJournal, read_artifact
    from domain_kg.revisions.projections import EmbeddedDocument

    if job.kind != "restore_material":
        raise inaccessible_reference()
    access = await queue.authorize(job)
    raw = await queue._current(job)
    parent = await PublicationJournal(queue).attempt(access, UUID(raw["repair_parent_manifest_id"]))
    permit = await restoration_permit(queue, access, job)
    if (
        not isinstance(permit, RestoreReadPermit)
        or parent["state"] != "accepted"
        or (
            parent["source"] != permit.source.model_dump(mode="json")
            or parent["id"] != str(permit.projection_manifest_id)
        )
    ):
        raise DomainError(ErrorCode.VERSION_CONFLICT, "The retained restoration parent changed.")
    checkpoint = Checkpoint.model_validate(parent["embedded_checkpoint"])
    body = await read_artifact(queue, access, checkpoint, permit.source, restoration=permit)
    await verify_restoration_original(queue, access, permit)
    if hashlib.sha256(body).hexdigest() != parent["batch"]["content_hash"]:
        raise DomainError(
            ErrorCode.PROJECTION_INCONSISTENT, "The retained source artifact differs."
        )
    document = EmbeddedDocument.model_validate_json(body)
    if (document.org_id, document.kg_id, document.material_id, document.version_id) != (
        job.org_id,
        job.kg_id,
        UUID(raw["material_id"]),
        job.target_id,
    ):
        raise inaccessible_reference()
    return await queue.store_artifact(job, "embedding", input_hash, body)


async def reprocessing_permit(
    queue: JobQueue, access: AccessContext, job: LeasedJob
) -> ReprocessReadPermit | None:
    if job.kind != "reprocess_material":
        return None
    permit = await restoration_permit(queue, access, job)
    if not isinstance(permit, ReprocessReadPermit):
        raise inaccessible_reference()
    return permit


async def verify_restoration_original(
    queue: JobQueue, access: AccessContext, permit: MaterialReadPermit
) -> None:
    from domain_kg.models import ScopedRef
    from domain_kg.storage.blobs import BlobStore

    source = permit.source
    await authorize_restore_read(queue, access, source, permit)
    stores = await queue.router.for_sources(access, source.kg_id)
    version = await stores.authority.get_material_version(
        ScopedRef(org_id=source.org_id, kg_id=source.kg_id, id=source.version_id)
    )
    raw = await stores.authority._database.version_blobs.find_one(
        {
            "org_id": str(source.org_id),
            "kg_id": str(source.kg_id),
            "id": str(source.version_id),
            "material_id": str(source.material_id),
            "state": {"$in": ["staged", "accepted", "retained"]}
            if isinstance(permit, ReprocessReadPermit)
            else {"$in": ["accepted", "retained"]},
        }
    )
    if version is None or version.material.id != source.material_id or raw is None:
        raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Retained original ownership differs.")
    try:
        blob = BlobRef.model_validate(raw["blob"])
    except (ValidationError, KeyError, TypeError):
        raise DomainError(
            ErrorCode.PROJECTION_INCONSISTENT, "The original ledger is invalid."
        ) from None
    if (blob.org_id, blob.kg_id, blob.material_id, blob.version_id) != (
        source.org_id,
        source.kg_id,
        source.material_id,
        source.version_id,
    ):
        raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Retained original ownership differs.")
    async for _ in BlobStore(stores.blobs).open(blob):
        pass
    await authorize_restore_read(queue, access, source, permit)
