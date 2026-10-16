"""Narrow current-policy authorization for reading retained data during restoration."""

from datetime import UTC, datetime

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.queue import JobQueue
from domain_kg.materials.models import RestoreReadPermit
from domain_kg.models import SourceVersion
from domain_kg.revisions.projections import AcceptedProjection


async def authorize_restore_read(
    queue: JobQueue, access: AccessContext, source: SourceVersion, permit: RestoreReadPermit
) -> None:
    permit = RestoreReadPermit.model_validate(permit)
    current = await queue.policy.revalidate(access, action=Action.REPLACE)
    if (
        (permit.org_id, permit.kg_id) != (current.org_id, source.kg_id)
        or source.org_id != current.org_id
        or permit.source != source
        or source.kg_id not in current.kg_ids
    ):
        raise inaccessible_reference()
    namespace = {"org_id": str(source.org_id), "kg_id": str(source.kg_id)}
    intent = await queue.database.material_commands.find_one(
        namespace | {"id": str(permit.command_id), "operation": "restore", "state": "pending"}
    )
    if (
        intent is None
        or intent.get("source") != source.model_dump(mode="json")
        or intent.get("expected_revision") != permit.expected_revision
        or any(
            intent.get("actor", {}).get(key) != getattr(current.principal, key)
            for key in ("issuer", "subject", "kind")
        )
    ):
        raise inaccessible_reference()
    now = datetime.now(UTC)
    if intent["expires_at"].replace(tzinfo=UTC) <= now:
        raise DomainError(ErrorCode.VERSION_CONFLICT, "The restoration read window expired.")
    material = await queue.policy._material_acl_record(current, source.kg_id, source.material_id)
    if material is None:
        raise inaccessible_reference()
    if (
        material["revision"] != permit.expected_revision
        or material.get("active_version_id") != str(source.version_id)
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
            "published_manifest_id": str(permit.view_manifest_id),
            "generation": str(permit.generation),
            "visibility_revision": permit.visibility_revision,
        }
    )
    binding = await queue.database.publication_bindings.find_one(
        namespace
        | {
            "view_manifest_id": str(permit.view_manifest_id),
            "owner_key": f"source:{source.material_id}",
            "binding.manifest_id": str(permit.projection_manifest_id),
        }
    )
    if kg is None or binding is None:
        raise DomainError(ErrorCode.POLICY_CHANGED, "The retained publication changed.")
    accepted = AcceptedProjection.model_validate(binding["binding"])
    if accepted.source != source or accepted.generation != permit.generation:
        raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Retained source ownership differs.")
