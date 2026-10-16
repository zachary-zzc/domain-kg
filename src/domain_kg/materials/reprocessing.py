"""Select the authenticated initial original without inventing an accepted version."""

from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import AccessContext
from domain_kg.jobs.queue import JobQueue
from domain_kg.materials.models import BlobRef
from domain_kg.models import ScopedRef, SourceVersion


async def unpublished_source(
    queue: JobQueue,
    access: AccessContext,
    material: ScopedRef,
    session: AsyncClientSession | None = None,
) -> SourceVersion:
    stores = await queue.router.for_sources(access, material.kg_id)
    namespace = {"org_id": str(material.org_id), "kg_id": str(material.kg_id)}
    rows = (
        await stores.authority._database.version_blobs.find(
            namespace | {"material_id": str(material.id)}, session=session
        )
        .limit(2)
        .to_list()
    )
    if len(rows) != 1:
        raise DomainError(
            ErrorCode.REVISION_NOT_READY, "The unpublished original is unavailable or ambiguous."
        )
    row = rows[0]
    blob = BlobRef.model_validate(row["blob"])
    source = SourceVersion(
        org_id=material.org_id,
        kg_id=material.kg_id,
        material_id=material.id,
        version_id=row["id"],
    )
    version = await stores.authority._require(
        "material_versions",
        ScopedRef(org_id=material.org_id, kg_id=material.kg_id, id=source.version_id),
        session=session,
    )
    await stores.authority.require_version(
        ScopedRef(org_id=material.org_id, kg_id=material.kg_id, id=source.version_id),
        session=session,
    )
    origin = await queue.database.jobs.find_one(
        namespace
        | {"kind": "ingest", "material_id": str(material.id), "target_id": str(source.version_id)},
        session=session,
    )
    command = await queue.database.material_commands.find_one(
        namespace | {"id": origin.get("command_id") if origin else None, "state": "accepted"},
        session=session,
    )
    if (
        row["state"] != "staged"
        or version["material"] != material.model_dump(mode="json")
        or any(
            getattr(blob, field) != getattr(source, field) for field in SourceVersion.model_fields
        )
        or origin is None
        or command is None
        or any(
            command.get("receipt", {}).get(field) != str(value)
            for field, value in (
                ("material_id", material.id),
                ("version_id", source.version_id),
                ("job_id", origin["id"]),
            )
        )
    ):
        raise DomainError(
            ErrorCode.PROJECTION_INCONSISTENT, "The initial original ownership differs."
        )
    return source
